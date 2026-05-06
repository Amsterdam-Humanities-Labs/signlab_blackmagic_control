# Spec: periodic sync of camera clips → SignCollect research drive

**Audience:** the next Claude session that will write `scripts/sync_clips.py`
(or equivalent) implementing this. Read end-to-end before coding.

## Goal

Every 12 hours, copy every `.braw` clip currently on the Blackmagic camera's
USB disk into:

```
<signcollect_root>/AIHR-FGW-TEST-SIGNLAB/blackmagic_files/<MMDD>/<filename>
```

where `<MMDD>` is the **month+day extracted from the filename** (e.g.
`1003_05061251_C011.braw` → folder `0506`). Final delivery to the research
drive **must use `rsync`** so we get atomic, resumable, checksum-validated
copies across the network mount.

Do **not** delete from the camera. The camera's USB disk is the source of
truth; the research drive is the durable archive.

## Hard requirements

1. Pulls clips through the **bmcam server** (`http://HOST:8000/api/...`),
   not directly from the camera. The server already holds Basic-auth creds
   and exposes the file proxy.
2. Rsync, not `cp`, to the research drive.
3. Idempotent: re-running the script must not re-download or re-rsync files
   that are already at the destination with the same size.
4. Survives transient failures (camera unreachable, drive unmounted, partial
   download): log and continue on the next iteration.
5. Loop interval **12 hours**, but expose it as a config var so you can
   shorten it during testing.

## What the bmcam server already gives you

All endpoints documented at `http://HOST:8000/docs` (Swagger). The three you
need:

### 1. List mounts

```
GET /api/mounts  →  [{"name":"usb/UNTITLED","type":"directory","mtime":"..."}]
```

The first entry's `name` is the mount path you'll use everywhere below
(typically `usb/UNTITLED` for an attached USB drive on this body — but do
**not** hard-code it; read it from `/api/mounts` each cycle, since after a
disk swap the volume label changes).

### 2. List files inside a mount

```
GET /api/mounts/{mount_path}
  →  [{"name":"1003_05061251_C011.braw","type":"file",
       "size":204159088,"mtime":"Tue, 06 May 2026 12:51:39"}, ...]
```

`type` is either `"file"` or `"directory"`. Recurse into directories if the
camera ever writes nested folders (current firmware writes flat). The
`mtime` is HTTP-date format; you don't need to parse it for the date
folder — that comes from the filename.

### 3. Stream-download a file

```
GET /api/download/{full_path}
```

`full_path` is `mount_path + '/' + filename` (e.g.
`usb/UNTITLED/1003_05061251_C011.braw`). Response is a binary stream with
`Content-Length` set; honor it for progress reporting and integrity
checking. URL-encode each path segment.

There is no `HEAD` and no `Range` support, so you can't resume partial
downloads — treat any download interrupted mid-flight as failed and
re-download next cycle. Implement this as: download to
`<staging>/.tmp.<filename>`, then atomic-rename to `<staging>/<filename>`
only on success.

The server is a thin proxy. Pulling a 5 GB clip through it takes the same
time as pulling directly from the camera — there's no buffering layer.

## Filename grammar

Default pattern (camera auto-generated):

```
<reel:4 digits>_<MMDDHHMM:8 digits>_C<NNN:3 digits>.braw
```

Examples seen in the wild:
```
1001_04221308_C001.braw   → date folder 0422
1003_05061251_C011.braw   → date folder 0506
```

Custom clip names (set via the `clipName` field on record start) bypass
this pattern — e.g. `MyScene_Take1.braw`. You will encounter these.

**Date-folder algorithm:**

```python
import re
DATE_RE = re.compile(r"^\d{4}_(\d{4})\d{4}_C\d{3}\.[A-Za-z0-9]+$")

def date_folder(name: str, mtime_iso: str) -> str:
    m = DATE_RE.match(name)
    if m:
        return m.group(1)         # "0506"
    # custom-named clip — fall back to file mtime from the API listing
    # parsed as HTTP-date format
    from email.utils import parsedate_to_datetime
    dt = parsedate_to_datetime(mtime_iso)
    return f"{dt.month:02d}{dt.day:02d}"
```

**File-type filter:** only sync `*.braw` (case-insensitive). The disk also
contains random user files (`SignSegmentation_full.zip`, `System Volume
Information/`, etc.) — skip anything that isn't a clip. If you ever need
ProRes/MOV support, add `.mov` and `.mp4` to the allow-list, but don't make
it a wildcard.

## Destination layout

```
{SIGNCOLLECT_ROOT}/AIHR-FGW-TEST-SIGNLAB/blackmagic_files/
├── 0422/
│   ├── 1001_04221308_C001.braw
│   └── 1001_04221312_C002.braw
├── 0506/
│   └── 1003_05061251_C011.braw
└── ...
```

`SIGNCOLLECT_ROOT` is configurable. The user's research drive is mounted on
macOS — typical paths to try (in order): `/Volumes/SignCollect`,
`/Volumes/signcollect`, `/Volumes/SignCollect Research`. Surface the actual
mount path as a CLI flag / env var; **fail fast** with a clear error if the
path doesn't exist or isn't writable, rather than silently creating the
folders on the local disk.

## Pipeline

Two-stage. Don't try to rsync directly from HTTP.

```
camera USB  ──/api/download──►  local staging dir  ──rsync──►  research drive
```

### Stage 1: download new clips into staging

```
STAGING = ~/bmcam_sync_staging/
```

For each clip on the camera:

1. Compute `date = date_folder(name, mtime)`.
2. `dest_path = STAGING / date / name`.
3. If `dest_path.exists()` and `dest_path.stat().st_size == api_listing.size`,
   skip — already downloaded.
4. Else:
   a. Stream `GET /api/download/...` into `STAGING / date / .tmp.<name>`.
   b. Verify written byte count matches `Content-Length`.
   c. `os.replace(.tmp.<name>, <name>)` on success; delete `.tmp.<name>` on
      any error.

Use `requests.get(..., stream=True)` and iterate `iter_content(64 KiB)`. Set
a per-request connect+read timeout (e.g. 30 s) but **no overall timeout** —
a 5 GB clip on USB3 can take many minutes.

### Stage 2: rsync staging → research drive

After all downloads finish (even if some failed — rsync only what's there):

```bash
rsync -av --partial --partial-dir=.rsync-partial \
      --omit-dir-times --no-perms --no-group --no-owner \
      --human-readable --stats \
      "$STAGING"/ \
      "$SIGNCOLLECT_ROOT/AIHR-FGW-TEST-SIGNLAB/blackmagic_files/"
```

Why these flags:

| flag | reason |
|---|---|
| `-a` | recursive + preserve mtime (so date folders survive) |
| `-v` + `--stats` | log lines per file + final summary, easy to grep |
| `--partial --partial-dir=.rsync-partial` | resume on next run if killed mid-copy |
| `--omit-dir-times --no-perms --no-group --no-owner` | network mounts (SMB/AFP) don't faithfully preserve these and rsync will spew warnings otherwise |
| `--human-readable` | log sizes as MB/GB |

Trailing slash on `"$STAGING/"` is **load-bearing** (rsync semantics: copy
*contents* of staging, not staging itself).

After a successful rsync, you may optionally prune staging files older than
N days to keep local disk usage bounded — but **only delete a staging file
if you've verified the destination has the same size**. Default: keep
staging untouched (it's a free local cache that lets the next run skip
downloads instantly).

## Loop

```python
INTERVAL_SECONDS = 12 * 3600

while True:
    started = time.monotonic()
    try:
        run_once()
    except Exception:
        log.exception("sync cycle failed")
    elapsed = time.monotonic() - started
    sleep_for = max(60, INTERVAL_SECONDS - elapsed)
    log.info("sleeping %.0f s until next cycle", sleep_for)
    time.sleep(sleep_for)
```

`run_once()` is the full Stage 1 + Stage 2 pipeline. Catch top-level
exceptions there — never let one bad cycle take down the loop.

Handle `SIGINT`/`SIGTERM` cleanly: on signal, finish the current
file's download, flush logs, then exit. Don't tear down mid-write.

## Pre-flight checks (run at startup AND at the top of each cycle)

1. `BMCAM_URL` reachable: `GET /api/health` → 200 with
   `{"status":"ok","cameraHost":...}`.
2. Camera has a mount: `GET /api/mounts` → non-empty array. Empty = no
   USB disk inserted; log a warning and skip this cycle (don't crash).
3. `SIGNCOLLECT_ROOT` exists, is a directory, is writable. If not, log
   error and skip cycle (don't crash). Network drives unmount themselves.
4. Staging dir exists and is writable; `mkdir -p` it.
5. `rsync` is on `PATH` (`shutil.which("rsync")`).

If 1 or 3 fail at startup, exit non-zero with a clear message — those are
config errors, not transient. If they fail mid-loop, just skip the cycle.

## Configuration

Read from env vars with sensible defaults. CLI args override.

| var | default | what |
|---|---|---|
| `BMCAM_URL` | `http://localhost:8000` | bmcam server base URL |
| `BMCAM_API_KEY` | unset | sent as `X-API-Key` header if set |
| `SIGNCOLLECT_ROOT` | *(required)* | research-drive mount root |
| `STAGING_DIR` | `~/bmcam_sync_staging` | local cache |
| `SYNC_INTERVAL_SECONDS` | `43200` | loop interval (12 h) |
| `SYNC_LOG_FILE` | `~/bmcam_sync.log` | rotating file log |
| `SYNC_DRY_RUN` | `0` | if `1`, list what would be done but don't download or rsync |

## Logging

Use `logging` with two handlers: stdout (DEBUG when `--verbose`, else INFO)
and a `RotatingFileHandler` on `SYNC_LOG_FILE` (10 MB × 5 backups). Format:

```
2026-05-06 13:00:00 INFO  cycle start
2026-05-06 13:00:01 INFO  found 14 clips on usb/UNTITLED
2026-05-06 13:00:01 INFO  → 1003_05061251_C011.braw  (194.7 MB) → 0506/
2026-05-06 13:00:42 INFO    downloaded in 41.2 s (4.7 MB/s)
2026-05-06 13:01:30 INFO  rsync: 14 files, 2.3 GB transferred, 0 errors
2026-05-06 13:01:30 INFO  cycle done in 90 s, sleeping 43110 s
```

Log every download with size + duration so the user can track throughput
trends.

## Test plan

You can't unit-test rsync to a real network drive in CI, but you should:

1. **Filename parsing** — table-driven tests of `date_folder()` for:
   - `1003_05061251_C011.braw` → `0506`
   - `1001_04221308_C001.braw` → `0422`
   - `MyScene_Take1.braw` (custom name) → derived from supplied mtime
   - `random_user_file.zip` → never reached (filtered earlier)
2. **File-type filter** — only `.braw` (and any future allow-list members)
   pass through.
3. **Idempotency** — given a staging file with the listed size, the
   downloader skips. Mock `requests.get`.
4. **Dry-run** — `SYNC_DRY_RUN=1` produces no filesystem writes and no
   subprocess calls.

For manual end-to-end: point `SIGNCOLLECT_ROOT` at a tmpdir, run with
`SYNC_INTERVAL_SECONDS=60`, watch one cycle, kill, inspect tree.

## Suggested layout

```
scripts/sync_clips.py        # the daemon (entry point)
scripts/sync_clips_test.py   # the unit tests
```

Add a `console_scripts` entry in `pyproject.toml`:

```
[project.scripts]
bmcam = "bmcam.cli:main"
bmcam-sync = "scripts.sync_clips:main"
```

So the user can run `bmcam-sync --signcollect-root /Volumes/SignCollect`
once the package is `pip install -e .`'d.

## Don't do

- **Don't** read `/control/api/v1/...` directly from the camera. Go through
  the bmcam server — it handles Basic auth and survives the camera's
  occasional REST hiccups.
- **Don't** parse filenames any harder than the regex above. If a clip
  doesn't match, fall back to mtime; don't try to be clever.
- **Don't** delete clips from the camera, even ones you've successfully
  archived. That's a separate human decision.
- **Don't** assume the mount name is `usb/UNTITLED`. Read it from
  `/api/mounts` every cycle.
- **Don't** add a database, queue, or state file. The filesystem (staging
  dir + destination tree) is the state. Idempotency comes from
  size-comparing.
- **Don't** wrap rsync in a Python implementation. Shell out.
- **Don't** silently fail when the network drive isn't mounted — log it
  loudly and skip the cycle.

## When you're done

- `pytest scripts/sync_clips_test.py -q` is green.
- A dry-run cycle against the live camera prints the planned actions
  without touching disk.
- A real cycle into a tmpdir produces correct `MMDD/<filename>` layout.
- Then commit + push to the same `rem0g/blackmagic_control` repo on a
  feature branch (`sync-script` is a fine name).

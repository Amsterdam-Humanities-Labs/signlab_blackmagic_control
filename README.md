# signlab_blackmagic_control (`bmcam`)

CLI, Python library and HTTP server for driving the studio's Blackmagic Studio
Camera 6K Pro over the Camera Control REST API — start/stop a recording, set the
video format, browse and download the clips on the camera's disk.

## What it does

The camera exposes a REST API and a Web Media Manager on its own IP on the studio
LAN. `bmcam` wraps both behind one interface, in three layers:

- **`bmcam` CLI** — one-shot commands (`bmcam record start`, `bmcam files`,
  `bmcam --json status`) and a websocket event tail.
- **`bmcam` library** — the `Camera` class, used directly by other repos.
- **`bmcam serve`** — a FastAPI facade on port 8000 with a single-page web
  inspector, Swagger docs and websocket relays, so a browser or another machine
  can drive the camera without speaking the camera's own API. This is the
  interface `signlab_blackmagic_RD_sync` consumes to pull and delete clips.

A fourth, separate component lives in `blackmagic_pineapple_service/`: a
Zeroconf + websocket adapter that advertises the camera as a `_mocap._tcp.local.`
device and accepts the same `Start` / `Stop` / `SetName` commands as the OBS and
Shogun adapters, so the Pineapple discovery pipeline can start the Blackmagic
camera alongside the rest of a mocap take.

## Where it runs

**On a machine on the studio camera LAN — not on the web server.** Everything
here talks to `192.168.0.194` over plain HTTP/websockets and the server binds
`0.0.0.0:8000` for browsers on the same LAN; the signcollect core VPS has no
route to that subnet.

**The Vicon PC, in the Visualisation Lab** (confirmed 2026-09-09, signlab_signcollect-stack#28).

The repository carries code for both platforms, which is what made this
ambiguous from the source alone: `blackmagic_pineapple_service` documents its
install in PowerShell against `.venv\Scripts\python.exe`, matching the Windows
host it actually runs on, while `signlab_blackmagic_RD_sync` builds its
transcoder with a macOS-only script. Only the Windows path is deployed. Treat
the macOS branches as supported-but-unused unless you find a second deployment.

## Status

**Experimental**, and in daily-ish use: the CLI, library and server are tested
(33 mocked tests) and depended on by the research-drive sync, but nothing here is
installed as a service — `bmcam serve` is started by hand (see below) and the
Pineapple adapter is run from a shell.

## How to run it

Python ≥ 3.10 (3.12 in practice). Install into a venv, then either use the CLI
or start the server; there is no scheduler involved. **`signlab_pythonCron`, the
estate scheduler, has no job for this repo** — nothing runs it automatically.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/bmcam status                 # one-shot
.venv/bin/bmcam serve --bind 0.0.0.0 --port 8000
```

See [Install](#install), [CLI](#cli) and [HTTP API server](#http-api-server)
below for the full surface.

## Configuration

No config file is required and no credentials are committed — host and auth come
from flags or the environment:

| Variable | Purpose |
|---|---|
| `BMCAM_HOST` | camera IP (default `192.168.0.194`) |
| `BMCAM_USER` / `BMCAM_PASSWORD` | camera basic-auth credentials |
| `BMCAM_API_KEY` | require `X-API-Key` on the HTTP server (off by default) |
| `BMCAM_ALLOW_ORIGINS` | CORS allowlist for the HTTP server (default `*`) |
| `BMCAM_TIMEOUT`, `BMCAM_DEBUG` | request timeout; full tracebacks |

The camera's basic-auth user and password are set on the camera itself, in
**Setup → Network**. Ask the lab for the current pair rather than expecting them
in this repo. The Pineapple adapter can also read them from a YAML file — copy
`blackmagic_pineapple_service/config.example.yaml` and edit it locally.

## Dependencies

- A **Blackmagic Studio Camera 6K Pro** (or compatible body) on the LAN, with
  **Web Media Manager** and **REST API** switched on — see below.
- Python packages: `requests`, `click`, `websocket-client`, `fastapi`, `uvicorn`
  (plus `zeroconf`/`websockets` for the Pineapple adapter, see
  `blackmagic_pineapple_service/requirements.txt`).
- Consumed by **`signlab_blackmagic_RD_sync`**, which downloads and deletes clips
  through this server's HTTP API.
- The **Pineapple discovery pipeline** (separate repo) for the Zeroconf adapter.
- `CLAUDE.md` records what this camera's firmware does and does not expose
  (no livestream API, no network preview) — read it before adding features.

## Install

```
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The `bmcam` script is then on `.venv/bin/bmcam`.

## Enable the REST API on the camera (one-time)

The camera serves port 80 but returns 404 on every endpoint until the REST
service is enabled.

- On the camera: **Setup → Network → Web Media Manager: On**, and
  **REST API: On**.
- Or via the **Blackmagic Camera Setup** desktop app: check **Web Media
  Manager** and **REST Camera Control**.

After enabling, `bmcam status` should return a JSON body.

## CLI

```
bmcam [--host H] [--user U] [--password P] [--json] <command>

  status                              aggregate status (format + record + video params + media + last clip)
  record start | stop | state         recording control
  format get                          current video format
  format set --width W --height H --fps F [--codec C]
                                      patch video format (only provided fields)
  files [--limit N]                   list clips on active disk
  filename                            most recent clip filename
  storage                             active disk + working set
  stream [--events | --preview]       live events over websocket, or probe for network preview
```

Global flags come **before** the subcommand: `bmcam --json status` (not `bmcam status --json`).

Examples:

```
bmcam --json status
bmcam --user "$BMCAM_USER" --password "$BMCAM_PASSWORD" record start
bmcam format set --fps 50
bmcam files --limit 5
bmcam stream --events          # live JSON events, one per line
```

Auth and host can also come from the environment:

```
export BMCAM_HOST=192.168.0.194
export BMCAM_USER=<camera user>
export BMCAM_PASSWORD=<camera password>
bmcam status
```

`BMCAM_DEBUG=1` prints full stack traces.

## Exit codes

| Code | Meaning                                       |
|-----:|-----------------------------------------------|
| 0    | ok                                            |
| 1    | generic camera error (5xx)                    |
| 2    | REST API not serving (enable it on camera)    |
| 3    | network unreachable / timeout                 |
| 4    | bad arguments / bad request                   |

## Library

```python
from bmcam import Camera

with Camera("192.168.0.194") as cam:
    print(cam.status())
    cam.record_start()
    cam.set_format(frameRate="50")
```

## Tests

```
.venv/bin/pytest -q
```

Tests mock the REST endpoints with `responses`; no camera required.

## HTTP API server

Run a standalone HTTP server for a website to talk to.

**Foreground** (Ctrl-C to stop):

```bash
export BMCAM_HOST=192.168.0.194
export BMCAM_USER=<camera user>
export BMCAM_PASSWORD=<camera password>
.venv312/bin/bmcam serve --bind 0.0.0.0 --port 8000
```

**Background** (survives terminal close):

```bash
nohup .venv312/bin/bmcam serve --bind 0.0.0.0 --port 8000 \
      > /tmp/bmcam-server.log 2>&1 &
disown
```

Stop a backgrounded instance with `pkill -f 'bmcam serve'` or
`lsof -ti:8000 | xargs kill`.

URLs:

- **Web inspector**: http://localhost:8000/ — one-page dashboard with record buttons (with optional scene name), video format and parameter dropdowns, storage, clip index, file browser with download + delete buttons, live camera event log, and server log.
- **Swagger UI**: http://localhost:8000/docs — interactive API browser with full request/response schemas (codec/fps enums, ISO/gain/WB ranges, etc.).

Endpoints (JSON):

| Method | Path | Purpose |
|---|---|---|
| GET    | `/api/health`           | liveness |
| GET    | `/api/status`           | aggregate status |
| GET    | `/api/record`           | current record state |
| POST   | `/api/record/start`     | start recording (optional `{clipName}`) |
| POST   | `/api/record/stop`      | stop recording |
| GET    | `/api/format`           | current video format |
| PATCH  | `/api/format`           | change `{codec, frameRate, width, height, offSpeed*}` |
| GET    | `/api/supportedFormats` | allowed (resolution × codec × fps) matrix on this body |
| GET    | `/api/files?limit=N`    | clip index (from `/timelines/0`) |
| GET    | `/api/filename`         | last clip filename |
| GET    | `/api/storage`          | active disk + working set |
| GET    | `/api/video/{name}`     | iso/shutter/whiteBalance/whiteBalanceTint/gain/ndFilter |
| PUT    | `/api/video/{name}`     | set one of the above (typed body per parameter) |
| GET    | `/api/mounts`           | list disks (Web Media Manager) |
| GET    | `/api/mounts/{path}`    | list directory contents |
| GET    | `/api/download/{path}`  | stream-download a file |
| DELETE | `/api/mounts/{path}`    | delete a file or directory on the camera disk (irreversible) |
| GET    | `/api/logs?limit=N`     | recent server log entries |
| WS     | `/api/logs/ws`          | live server log stream |
| WS     | `/api/events`           | relays camera event websocket |

Browser example:

```js
// start recording
await fetch("http://localhost:8000/api/record/start", { method: "POST" });
// subscribe to live events
const ws = new WebSocket("ws://localhost:8000/api/events");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

Auth and CORS:

- `--api-key SECRET` (or `BMCAM_API_KEY=SECRET`) requires the browser to send
  `X-API-Key: SECRET` on every request. Off by default for LAN use.
- `--allow-origin https://your.site` (repeatable, or comma‑separated via
  `BMCAM_ALLOW_ORIGINS`) restricts CORS. Default `*`.

The server is a thin facade — it does not cache or buffer. Each request hits
the camera live.

## Notes on preview / live video

The REST API does not expose live video preview on Pocket Cinema Camera bodies
(including the 6K Pro). `bmcam stream --preview` probes for an MJPEG/HLS
preview endpoint; if absent, it prints a clear message. Use HDMI/SDI output
for a live picture. `bmcam stream --events` always works once the API is
enabled — it streams JSON status events over the camera's websocket.

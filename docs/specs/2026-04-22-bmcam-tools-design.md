# bmcam — Tools for controlling a Blackmagic 6K Pro via REST

Date: 2026-04-22
Target device: 192.168.0.194 (Blackmagic Camera Control REST API v1)

## Goals

Provide a small Python package plus a CLI (`bmcam`) that can:

1. Start / stop recording
2. Get all current video-related status (format, ISO, shutter, WB, gain, ND, record state, timecode)
3. Get / set video format (resolution, frame rate, codec)
4. Get the current / last clip filename
5. Get storage info (disks in the working set, active disk, free space)
6. Get a stream — live metadata stream over the API's websocket, plus a best-effort video preview helper

## Non-goals

- Full control of color correction, lens, audio (the client library exposes them generically, but the CLI ships only the six targets above).
- Cross-camera fleet management.
- A GUI.

## Preconditions

- The REST API is served by the camera's Web Media Manager. It must be enabled:
  - On the camera: Setup → Network → **Web Media Manager: On** and **REST API: On**.
  - Via Blackmagic Camera Setup (desktop): check **Web Media Manager** and **REST Camera Control**.
- Camera OS should be 8.x+ (verified against spec published August 2025).
- 192.168.0.194 currently responds on port 80 but returns 404 for every `/control/api/v1/*` path, confirming the service is off. Enabling it is part of the README quick-start, not the tool's responsibility.

## Architecture

```
bmcam/
  __init__.py      # public re-exports
  client.py        # Camera: thin typed wrapper over requests.Session
  cli.py           # Click entry point
  stream.py        # websocket event loop + preview helper
  errors.py        # CameraError, ApiDisabledError, NotFoundError, ...
  models.py        # TypedDict / dataclass response shapes
tests/
  test_client.py   # responses-mocked unit tests
  test_cli.py      # CliRunner tests
```

One-sentence boundaries:

- **client.py** speaks REST; knows nothing about argparse or stdout.
- **cli.py** turns CLI args into client calls and formats output (table or JSON).
- **stream.py** is the only module that opens long-lived sockets; used by both the library and the `bmcam stream` command.

## REST endpoints consumed

All relative to `http://<host>/control/api/v1`.

| Concern | Method | Path | Body |
|---|---|---|---|
| System info | GET | `/system` | — |
| Video format | GET/PUT | `/system/format` | `{codec, frameRate, recordResolution, sensorResolution, ...}` |
| Record state | GET | `/transports/0/record` | — |
| Start record | PUT | `/transports/0/record` | `{"recording": true}` |
| Stop record | PUT | `/transports/0/record` | `{"recording": false}` |
| Timecode | GET | `/transports/0/timecode` | — |
| Clip list | GET | `/timelines/0` | — |
| Working set | GET | `/media/workingset` | — |
| Active disk | GET | `/media/active` | — |
| Video ISO | GET/PUT | `/video/iso` | `{"iso": int}` |
| Shutter | GET/PUT | `/video/shutter` | `{"shutterAngle": number}` or `{"shutterSpeed": int}` |
| White balance | GET/PUT | `/video/whiteBalance` | `{"whiteBalance": int}` |
| WB tint | GET/PUT | `/video/whiteBalanceTint` | `{"whiteBalanceTint": int}` |
| Gain | GET/PUT | `/video/gain` | `{"gain": int}` |
| ND filter | GET/PUT | `/video/ndFilter` | `{"stop": number}` |
| Events | WS | `/event/websocket` | subscribe messages |

Endpoints not present on a given camera model return 404; the client treats those as "unsupported capability" and the status aggregator degrades gracefully (field omitted, not error).

## CLI surface

```
bmcam --host 192.168.0.194 [--json] <command>

  status                       aggregate: system, format, record, video params, timecode
  record start                 PUT /transports/0/record {"recording": true}
  record stop                  PUT /transports/0/record {"recording": false}
  record state                 GET /transports/0/record
  format get                   GET /system/format
  format set --width W --height H --fps F [--codec C]
                               PUT /system/format (only provided fields)
  files [--limit N]            GET /timelines/0; print clip names + current timecode
  filename                     last-recorded clip name (from timelines list)
  storage                      GET /media/workingset + /media/active
  stream [--events|--preview]  events: websocket metadata stream
                               preview: probe /preview MJPEG, falls back with message
```

Flags:

- `--host` default `192.168.0.194` (override via `BMCAM_HOST`).
- `--json` switches human output to one JSON document per command.
- Non-zero exit codes: 2 (api disabled / 404), 3 (network error), 4 (invalid args), 1 (generic).

## Data flow for key commands

### `status`

1. Parallel GETs (one `requests.Session`, sequential is fine — latency on a LAN is a few ms):
   `/system`, `/system/format`, `/transports/0/record`, `/transports/0/timecode`,
   `/video/iso`, `/video/shutter`, `/video/whiteBalance`, `/video/whiteBalanceTint`,
   `/video/gain`, `/video/ndFilter`, `/media/active`, `/media/workingset`.
2. Each call wrapped by `_safe_get` which converts 404 → `None` so unsupported endpoints are silently omitted.
3. Aggregate into a single dict; render as a table (human) or JSON.

### `format set`

1. Fetch current `/system/format`.
2. Merge user-provided fields over current body.
3. PUT merged body. The API rejects invalid combinations with 4xx — surface the error message verbatim.
4. Re-fetch and print the new format.

### `stream --events`

- Open `ws://<host>/control/api/v1/event/websocket`.
- On connect, send a subscription message listing the property paths to watch (record, timecode, format, video/*). The wire format is the camera's published protocol (one JSON message per change).
- Print each event as a single-line JSON record to stdout. SIGINT ends the loop cleanly.

### `stream --preview`

- Probe `/control/api/v1/preview` (HEAD). If it returns a content-type of `multipart/x-mixed-replace` (MJPEG) or `application/vnd.apple.mpegurl` (HLS), spawn `ffplay` / `vlc` / save to file depending on flags.
- If the endpoint is absent (many PCC-class bodies), print: "this model does not expose network video preview; connect HDMI/SDI."

## Error handling

- Unified exception tree in `errors.py`.
- `CameraError(cause, url, status)` is the base.
- HTTP 404 on a namespace root → `ApiDisabledError` with remediation text.
- HTTP 4xx with body → `BadRequestError(message_from_body)`.
- `requests.ConnectionError` → `UnreachableError`.
- CLI catches these at the top level, maps to exit codes, prints a single-line reason. No stack traces unless `BMCAM_DEBUG=1`.

## Testing

- Unit tests use `responses` to mock each endpoint. Covers:
  - `status` aggregation with mixed 200/404 responses.
  - `record start/stop` payloads.
  - `format set` merge-and-PUT logic.
  - `storage` combines `media/active` and `media/workingset`.
  - CLI exit codes on each error class.
- A single live smoke test (pytest marker `live`, skipped by default) hits `$BMCAM_HOST/system` and asserts a 200.

## Open questions (deferred, not blocking)

- Exact websocket subscription framing: will be finalized against a live camera once REST is enabled. Until then, `stream --events` is scaffolded but marked experimental.
- Whether the 6K Pro exposes `/preview` — unlikely; tool handles absence gracefully.

## Out of scope

- Authentication (the REST API on current Blackmagic firmware is unauthenticated on the LAN).
- HTTPS (port 443 refused connection during probe; HTTP only).
- Firmware updates, LUT upload, audio routing.

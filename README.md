# bmcam

CLI and Python library for controlling a Blackmagic camera over the Camera
Control REST API. Targets a Blackmagic 6K Pro at `192.168.0.194` by default;
works with any Blackmagic camera running Camera OS with the REST API enabled.

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
bmcam --user vislab --password 'Blabla100?' record start
bmcam format set --fps 50
bmcam files --limit 5
bmcam stream --events          # live JSON events, one per line
```

Auth and host can also come from the environment:

```
export BMCAM_HOST=192.168.0.194
export BMCAM_USER=vislab
export BMCAM_PASSWORD='Blabla100?'
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

Run a standalone HTTP server for a website to talk to:

```
export BMCAM_USER=vislab
export BMCAM_PASSWORD='Blabla100?'
bmcam serve --bind 0.0.0.0 --port 8000
```

- **Web inspector**: http://localhost:8000/ — one-page dashboard with record buttons, video format controls, video/lens parameters, storage, clips, and a live websocket event log.
- **Swagger UI**: http://localhost:8000/docs — interactive API browser.

Endpoints (JSON):

| Method | Path | Purpose |
|---|---|---|
| GET   | `/api/health`           | liveness |
| GET   | `/api/status`           | aggregate status |
| GET   | `/api/record`           | current record state |
| POST  | `/api/record/start`     | start recording |
| POST  | `/api/record/stop`      | stop recording |
| GET   | `/api/format`           | current video format |
| PATCH | `/api/format`           | change `{codec, frameRate, width, height, ...}` |
| GET   | `/api/files?limit=N`    | clip list |
| GET   | `/api/filename`         | last clip filename |
| GET   | `/api/storage`          | active disk + working set |
| GET   | `/api/video/{name}`     | iso/shutter/whiteBalance/whiteBalanceTint/gain/ndFilter |
| PUT   | `/api/video/{name}`     | set one of the above |
| WS    | `/api/events`           | relays camera event websocket |

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

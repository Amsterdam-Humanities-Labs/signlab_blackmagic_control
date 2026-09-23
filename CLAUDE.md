# CLAUDE.md

Agent hints. Setup, CLI and config: `README.md`. Vendor API snapshot: `docs/camera-api/`.

## Camera (Studio Camera 6K Pro, `192.168.0.194`)
- REST base `http://<host>/control/api/v1`; Web Media Manager at `/` with a `/mounts/...` JSON API; events on `/control/api/v1/event/websocket`.
- The camera's own `/control/documentation.html` is authoritative for what this firmware serves.
- Not on this firmware: livestream (`/livestreams/*` → 404), any preview/snapshot/MJPEG/HLS, `/slates/*`. Only `clipName` on `PUT /transports/0/record` names clips.

## Quirks
- `/system` returns 204 when the API is up: treat 204 as ok, only 404 as "API disabled".
- `/system/codecFormat` returns 501; the client treats 501 like 404.
- Recording can auto-stop within seconds on USB3 disks (write throughput vs BRaw 8:1 6K50): drop to `BRaw:12_1` or lower res/fps.
- WMM mount names differ from `deviceName` (`usb4352` vs `usb/UNTITLED`): derive from `/mounts/`, not the REST device name.

## Code
- `bmcam/client.py` `Camera` (REST + WMM) · `server.py` FastAPI (`bmcam serve`) · `cli.py` Click CLI · `stream.py` event stream · `webui/index.html` inspector.
- Tests: `.venv/bin/pytest -q`, all mocked with `responses` (no camera needed).
- Never put camera credentials in tracked files; use `BMCAM_USER` / `BMCAM_PASSWORD`.

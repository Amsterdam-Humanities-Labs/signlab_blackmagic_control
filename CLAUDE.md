# CLAUDE.md

Project notes for Claude sessions working on this repo.

## Target camera

- **Model:** Blackmagic **Studio Camera 6K Pro**
- **IP:** `192.168.0.194` (default `BMCAM_HOST`)
- **Auth:** Basic — user `vislab`, password `Blabla100?`
- **REST API base:** `http://192.168.0.194/control/api/v1`
- **Web Media Manager:** `http://192.168.0.194/` (React SPA) + `/mounts/...` JSON API
- **Event websocket:** `ws://192.168.0.194/control/api/v1/event/websocket`

## Capabilities actually exposed by this camera's firmware

The camera's own `/control/documentation.html` is authoritative; it lists the
YAMLs this firmware serves:

```
EventControl, TransportControl, MediaControl, TimelineControl, SystemControl,
VideoControl, PresetControl, AudioControl, LensControl, ColorCorrectionControl,
Notification (asyncAPI)
```

Snapshot pulled to `docs/camera-api/`.

### Not currently exposed over REST on this unit

Even though the Studio Camera 6K Pro line *can* livestream, the REST endpoints
for it are **not present on the current firmware** of this unit:

- `/livestreams/0`, `/livestreams/0/start`, `/livestreams/platforms`, etc. →
  all return 404.
- No `LivestreamControl.yaml` in the camera's own API index.
- No `/preview`, `/snapshot`, `/thumbnail`, MJPEG, or HLS endpoint — no
  network video preview or still image pull.
- No `/slates/*` — only the `clipName` field on `PUT /transports/0/record`
  works for naming clips. Full digital-slate metadata (scene/take/reel/shotType)
  is not available.

If these are needed, check for a camera OS update — Blackmagic added the
Livestream API to the Studio line in later firmware revisions.

## Known quirks observed

- `/system` returns 204 No Content (not 404) when the API is reachable, so
  treat 204 as "ok" and only 404 as "API disabled".
- `/system/codecFormat` returns 501 "Not implemented" on this unit — the
  client treats 501 like 404.
- Recording sometimes auto-stops within seconds even with an active USB3 disk
  that has plenty of free space. Usually tied to sustained write throughput
  vs. BRaw 8:1 6K50 bandwidth — drop codec to `BRaw:12_1` or lower
  resolution/fps if it recurs.
- WMM mount names don't match `deviceName` — camera reports `usb4352`,
  WMM uses `usb/UNTITLED` (type/volume). Derive from `/mounts/` listing, not
  from the REST API device name.

## Package layout

- `bmcam/client.py` — `Camera` class wrapping REST + WMM.
- `bmcam/server.py` — FastAPI facade (`bmcam serve`), also serves the web UI.
- `bmcam/webui/index.html` — single-file inspector UI.
- `bmcam/cli.py` — `bmcam` Click CLI.
- `bmcam/stream.py` — websocket event stream + preview probe.
- `docs/camera-api/` — local copy of the camera's YAML docs + the Blackmagic
  PDF reference.

## Dev workflow

```bash
.venv312/bin/pytest -q           # 33 tests, all mocked with `responses`
.venv312/bin/bmcam serve          # runs FastAPI on :8000; needs BMCAM_API_KEY or --no-auth
```

Env vars: `BMCAM_HOST`, `BMCAM_USER`, `BMCAM_PASSWORD`, `BMCAM_API_KEY`,
`BMCAM_NO_AUTH`, `BMCAM_ALLOW_ORIGINS`, `BMCAM_TIMEOUT`, `BMCAM_DEBUG`.

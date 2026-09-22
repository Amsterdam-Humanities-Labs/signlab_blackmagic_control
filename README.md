# signlab_blackmagic_control (`bmcam`)
CLI, Python library and HTTP server for the studio's Blackmagic Studio Camera 6K Pro over its REST API: record, video format, clips on disk.

## What it does
- `bmcam` CLI: one-shot commands (`bmcam --json status`, `record start|stop`, `format get|set`, `files`, `storage`, `stream --events`). Global flags go before the subcommand. `bmcam --help` has the full list.
- `bmcam` library: `Camera` class (`with Camera("192.168.0.194") as cam: cam.record_start()`).
- `bmcam serve`: FastAPI facade (default `0.0.0.0:8000`) with a web inspector at `/`, Swagger at `/docs`, websocket relays. No caching; every request hits the camera. `DELETE /api/mounts/{path}` deletes on the camera disk. `signlab_blackmagic_RD_sync` uses it to list, download and delete clips.
- `blackmagic_pineapple_service/`: Zeroconf `_mocap._tcp.local.` + websocket adapter taking `Start`/`Stop`/`SetName`, so the Pineapple pipeline can start the camera with a mocap take. See its README.
- The camera has no livestream, preview or slate API on current firmware (see `CLAUDE.md`); use HDMI/SDI for a picture.

## Where it runs
- The Vicon PC (Windows) in the Visualisation Lab, on the studio camera LAN (camera `192.168.0.194`). Not on the web server: the VPS has no route to that subnet.

## Status
Experimental, in regular use. Nothing supervises it: `bmcam serve` and the Pineapple adapter are started by hand, and pythonCron has no job for them.

## How to run / deploy
```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"    # Python >= 3.10
.venv/bin/bmcam --json status
.venv/bin/bmcam serve --bind 0.0.0.0 --port 8000
.venv/bin/pytest -q                                              # mocked, no camera needed
```
One-time camera setup: Setup -> Network -> Web Media Manager On, REST API On (or tick Web Media Manager and REST Camera Control in Blackmagic Camera Setup). Until then every endpoint returns 404.
CLI exit codes: 0 ok, 1 camera 5xx, 2 REST API off, 3 unreachable/timeout, 4 bad request.

## Configuration
| Variable / flag | Purpose |
|---|---|
| `BMCAM_HOST` / `--host` | camera IP (default `192.168.0.194`) |
| `BMCAM_USER`, `BMCAM_PASSWORD` | camera basic auth (set on the camera under Setup -> Network) |
| `BMCAM_API_KEY` / `--api-key` | require `X-API-Key` on `bmcam serve`; off by default ([stack#31](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack/issues/31)) |
| `BMCAM_ALLOW_ORIGINS` / `--allow-origin` | CORS allowlist (default `*`) |
| `BMCAM_TIMEOUT`, `BMCAM_DEBUG` | request timeout; full tracebacks |

The Pineapple adapter can also read `blackmagic_pineapple_service/config.example.yaml` (copy it locally).

## Dependencies
- Blackmagic Studio Camera 6K Pro (or compatible body) with the REST API on. Vendor API: `docs/camera-api/` (YAML specs and the PDF).
- Python: `requests`, `click`, `websocket-client`, `fastapi`, `uvicorn`; Pineapple adapter adds `zeroconf`, `websockets`.
- Used by `signlab_blackmagic_RD_sync` (HTTP) and the Pineapple discovery pipeline (Zeroconf).
- Stack overview: https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack

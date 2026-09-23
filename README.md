# signlab_blackmagic_control (`bmcam`)
CLI, Python library and HTTP server for the studio's Blackmagic Studio Camera 6K Pro. It uses the camera's REST API to control recording, the video format and the clips on disk.

## What it does
- `bmcam` CLI: one-shot commands such as `bmcam --json status`, `record start|stop`, `format get|set`, `files`, `storage`, `stream --events`. Global flags go before the subcommand. `bmcam --help` lists everything.
- `bmcam` library: the `Camera` class (`with Camera("192.168.0.194") as cam: cam.record_start()`).
- `bmcam serve`: a FastAPI server (default `0.0.0.0:8000`) with a web inspector at `/`, Swagger at `/docs` and websocket relays. It does not cache; every request goes to the camera. `DELETE /api/mounts/{path}` deletes on the camera disk. [signlab_blackmagic_RD_sync](https://github.com/Amsterdam-Humanities-Labs/signlab_blackmagic_RD_sync) uses it to list, download and delete clips.
- `blackmagic_pineapple_service/`: a Zeroconf (`_mocap._tcp.local.`) and websocket adapter that takes `Start`/`Stop`/`SetName`. The Pineapple pipeline uses it to start the camera together with a mocap take. See its README.
- Current firmware has no API for livestream, preview or slate (see `CLAUDE.md`). Use HDMI or SDI for a picture.

## Where it runs
The Vicon PC (Windows) in the Visualisation Lab, on the studio camera LAN (camera at `192.168.0.194`). Not on the core server: it has no route to that subnet.

## Status
Experimental, in regular use. Nothing supervises it: `bmcam serve` and the Pineapple adapter are started by hand, and pythonCron has no job for them.

## How to run / deploy
```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"    # Python >= 3.10
.venv/bin/bmcam --json status
BMCAM_API_KEY=... .venv/bin/bmcam serve --bind 0.0.0.0 --port 8000   # or --no-auth, LAN only
.venv/bin/pytest -q                                              # mocked, no camera needed
```
One-time camera setup: Setup -> Network -> Web Media Manager On, REST API On. Or tick Web Media Manager and REST Camera Control in Blackmagic Camera Setup. Until then every endpoint returns 404.
CLI exit codes: 0 ok, 1 camera 5xx, 2 REST API off, 3 unreachable or timeout, 4 bad request.

## Configuration
| Variable / flag | Purpose |
|---|---|
| `BMCAM_HOST` / `--host` | camera IP (default `192.168.0.194`) |
| `BMCAM_USER`, `BMCAM_PASSWORD` | camera basic auth (set on the camera under Setup -> Network) |
| `BMCAM_API_KEY` / `--api-key` | required by `bmcam serve`. Every `/api` route except `/api/health` needs `X-API-Key`. The web inspector asks once and keeps the key in a cookie. signlab_blackmagic_RD_sync sends the same variable |
| `BMCAM_NO_AUTH=1` / `--no-auth` | explicit opt-out for LAN use: serve without a key (logs a warning). With neither a key nor this flag, `serve` refuses to start |
| `--allow-origin` | CORS allowlist for `serve`, repeatable (default `*`). `serve` overwrites `BMCAM_ALLOW_ORIGINS` with it |
| `--timeout`, `BMCAM_DEBUG` | camera request timeout (default 5 s); full tracebacks on errors |

The Pineapple adapter reads `--config <yaml>` (template: `blackmagic_pineapple_service/config.example.yaml`). `BMCAM_HOST`, `BMCAM_USER`, `BMCAM_PASSWORD`, `BMCAM_TIMEOUT` and `BMCAM_SERVICE_*` override it.

## Dependencies
- Blackmagic Studio Camera 6K Pro (or a compatible body) with the REST API on. Vendor API: `docs/camera-api/` (YAML specs and the PDF).
- Python: `requests`, `click`, `websocket-client`, `fastapi`, `uvicorn`. The Pineapple adapter adds `zeroconf` and `websockets`.
- Used by [signlab_blackmagic_RD_sync](https://github.com/Amsterdam-Humanities-Labs/signlab_blackmagic_RD_sync) (HTTP) and the Pineapple discovery pipeline (Zeroconf).
- Stack overview: [signlab_signcollect-stack](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack).

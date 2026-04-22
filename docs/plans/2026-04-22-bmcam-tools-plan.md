# Implementation plan — bmcam tools

Date: 2026-04-22
Spec: [docs/specs/2026-04-22-bmcam-tools-design.md](../specs/2026-04-22-bmcam-tools-design.md)

## Phase 0 — Scaffold

- [ ] `pyproject.toml` with `name = "bmcam"`, Python ≥3.10, deps: `requests>=2.31`, `click>=8.1`, `websocket-client>=1.7`. Dev deps: `pytest`, `responses`.
- [ ] `bmcam/__init__.py` re-exports `Camera`, `CameraError`.
- [ ] Empty modules: `client.py`, `cli.py`, `stream.py`, `errors.py`, `models.py`.
- [ ] Entry point: `[project.scripts] bmcam = "bmcam.cli:main"`.
- [ ] `README.md` with quick start + precondition note (enable REST API on camera).
- [ ] `.gitignore`.

## Phase 1 — errors + models

- [ ] `errors.py`:
  `CameraError` (base, carries `url`, `status`, `payload`) and subclasses `ApiDisabledError`, `NotFoundError`, `BadRequestError`, `UnreachableError`.
- [ ] `models.py`: `TypedDict`s for `SystemInfo`, `VideoFormat`, `RecordState`, `Timecode`, `Disk`, `Clip`.

## Phase 2 — Client library

- [ ] `client.Camera(host, timeout=5.0)`:
  - Holds a `requests.Session`, base URL `http://{host}/control/api/v1`.
  - Internal `_get(path)` / `_put(path, json)` / `_safe_get(path, default=None)`.
  - Methods:
    - `system() -> SystemInfo`
    - `get_format() -> VideoFormat`
    - `set_format(**patch) -> VideoFormat` (merge + PUT + re-GET)
    - `record_state() -> RecordState`
    - `record_start() / record_stop()` (PUT `/transports/0/record`)
    - `timecode() -> Timecode`
    - `list_clips() -> list[Clip]` (from `/timelines/0`)
    - `current_filename() -> str | None` (last clip or active)
    - `workingset() -> list[Disk]`, `active_disk() -> Disk | None`
    - `video_param(name)` / `set_video_param(name, value)` generic for `iso`, `shutter`, `whiteBalance`, `whiteBalanceTint`, `gain`, `ndFilter`
    - `status() -> dict` aggregates all of the above using `_safe_get`.

## Phase 3 — CLI

- [ ] `cli.py` with Click group `bmcam`, options `--host` (env `BMCAM_HOST`, default `192.168.0.194`), `--json`.
- [ ] Subcommands matching spec: `status`, `record start|stop|state`, `format get|set`, `files`, `filename`, `storage`, `stream`.
- [ ] `format set` accepts `--width/--height/--fps/--codec`; passes only provided fields.
- [ ] Human renderer: a compact key/value block for scalar output; a small table for clips and disks. No external table deps.
- [ ] Top-level `try/except CameraError` → exit codes from spec.

## Phase 4 — Streaming helper

- [ ] `stream.EventStream(host)`:
  - Opens `ws://{host}/control/api/v1/event/websocket` via `websocket-client`.
  - On open, sends subscription message for the property list defined in the spec.
  - Yields parsed JSON events. Reconnects once on unexpected close (test manually, don't over-engineer).
- [ ] `stream --events` iterates `EventStream` and prints one JSON line per event.
- [ ] `stream --preview` HEADs `/preview`; if available invokes `ffplay` (configurable via `BMCAM_PLAYER`); else prints a clear message.

## Phase 5 — Tests

- [ ] `tests/test_client.py` (responses-mocked):
  - 200 path for each client method.
  - 404 on a single endpoint → `status()` omits that key, other keys present.
  - 404 on `/system` → `ApiDisabledError`.
  - `set_format` sends merged body.
  - `record_start`/`record_stop` send correct JSON.
- [ ] `tests/test_cli.py` with Click's `CliRunner`:
  - `bmcam status --json` is valid JSON.
  - `bmcam record start` exits 0 on 200, exits 2 on 404.
  - `bmcam format set --fps 23.976` passes only `frameRate` in the patch.
- [ ] `tests/test_live.py` with `@pytest.mark.live`; skipped unless `BMCAM_LIVE=1`.

## Phase 6 — Smoke + polish

- [ ] Run `bmcam status --host 192.168.0.194`.
- [ ] If 404 on `/system`, print the README snippet telling the user to enable the service.
- [ ] Write `README.md` usage examples (copy the CLI help and add "enable REST API" instructions from the spec).
- [ ] Final tidy pass: docstrings on the `Camera` methods only; no other comments unless a subtlety demands it.

## Sequencing

Phases 0 → 1 → 2 → 3 → 5 (tests for 2+3) → 4 → 5 (tests for 4) → 6.
Streaming (Phase 4) is isolated and can be skipped if the smoke test reveals unsupported endpoints; that does not block the rest of the tool.

## Done definition

- `bmcam status`, `record start/stop`, `format get/set`, `files`, `filename`, `storage` all return sensible output against a live camera with the REST API enabled.
- `bmcam stream --events` prints live JSON events (if websocket endpoint is present).
- Unit tests pass under `pytest -q`.
- README documents the "enable REST API" precondition.

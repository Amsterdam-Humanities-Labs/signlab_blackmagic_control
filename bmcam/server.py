from __future__ import annotations

import asyncio
import collections
import json as jsonlib
import logging
import os
import time
import urllib.parse
from typing import Any

from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_WEBUI_DIR = Path(__file__).parent / "webui"

from .client import Camera
from .errors import (
    ApiDisabledError,
    BadRequestError,
    CameraError,
    NoMediaError,
    NotFoundError,
    UnreachableError,
)
from .stream import event_stream


class _LogRing(logging.Handler):
    def __init__(self, maxlen: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self.buf: collections.deque[dict] = collections.deque(maxlen=maxlen)
        self.subscribers: set[asyncio.Queue[dict]] = set()
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        self.buf.append(entry)
        for q in list(self.subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass


_log_ring = _LogRing()
logger = logging.getLogger("bmcam")
logger.setLevel(logging.INFO)
if not any(isinstance(h, _LogRing) for h in logger.handlers):
    logger.addHandler(_log_ring)
# also echo to uvicorn stderr
if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _LogRing)
           for h in logger.handlers):
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_sh)


class FormatPatch(BaseModel):
    codec: str | None = None
    frameRate: str | None = None
    width: int | None = None
    height: int | None = None
    offSpeedEnabled: bool | None = None
    offSpeedFrameRate: float | None = None


class RecordStartBody(BaseModel):
    clipName: str | None = None


class Settings:
    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        timeout: float,
        api_key: str | None,
        allow_origins: list[str],
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.api_key = api_key
        self.allow_origins = allow_origins


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="bmcam server",
        description="HTTP facade over a Blackmagic camera's REST API. "
        "Wraps record/format/media controls for easy consumption from a website.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _require_key(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_key is None:
            return
        if x_api_key != settings.api_key:
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")

    def _camera() -> Camera:
        return Camera(
            host=settings.host,
            timeout=settings.timeout,
            username=settings.username,
            password=settings.password,
        )

    @app.exception_handler(ApiDisabledError)
    async def _h_api_disabled(_req: Request, exc: ApiDisabledError) -> JSONResponse:
        logger.error(f"api_disabled: {exc}")
        return JSONResponse(
            status_code=503,
            content={"error": "api_disabled", "detail": str(exc)},
        )

    @app.exception_handler(UnreachableError)
    async def _h_unreachable(_req: Request, exc: UnreachableError) -> JSONResponse:
        logger.error(f"unreachable: {exc}")
        return JSONResponse(
            status_code=504,
            content={"error": "unreachable", "detail": str(exc)},
        )

    @app.exception_handler(NotFoundError)
    async def _h_notfound(_req: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": str(exc)},
        )

    @app.exception_handler(BadRequestError)
    async def _h_bad(_req: Request, exc: BadRequestError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": str(exc), "payload": exc.payload},
        )

    @app.exception_handler(NoMediaError)
    async def _h_no_media(_req: Request, exc: NoMediaError) -> JSONResponse:
        logger.warning(f"no_media: {exc}")
        return JSONResponse(
            status_code=409,
            content={"error": "no_media", "detail": str(exc)},
        )

    @app.exception_handler(CameraError)
    async def _h_camera(_req: Request, exc: CameraError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": "camera_error", "detail": str(exc)},
        )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "cameraHost": settings.host}

    if _WEBUI_DIR.is_dir():
        @app.get("/", include_in_schema=False)
        def _webui_index() -> FileResponse:
            return FileResponse(_WEBUI_DIR / "index.html")

        app.mount(
            "/ui",
            StaticFiles(directory=str(_WEBUI_DIR), html=True),
            name="webui",
        )

    @app.get("/api/status", dependencies=[Depends(_require_key)])
    def get_status() -> dict:
        with _camera() as cam:
            return cam.status()

    @app.get("/api/record", dependencies=[Depends(_require_key)])
    def get_record() -> dict:
        with _camera() as cam:
            return cam.record_state() or {}

    @app.post("/api/record/start", dependencies=[Depends(_require_key)])
    def post_record_start(body: RecordStartBody = Body(default=RecordStartBody())) -> dict:
        clip_name = (body.clipName or "").strip() or None
        with _camera() as cam:
            logger.info(
                f"record/start requested (clipName={clip_name!r})"
                if clip_name
                else "record/start requested"
            )
            cam.record_start(clip_name=clip_name)
            state = cam.record_state() or {}
            logger.info(f"record/start → {state}")
            return state

    @app.post("/api/record/stop", dependencies=[Depends(_require_key)])
    def post_record_stop() -> dict:
        with _camera() as cam:
            logger.info("record/stop requested")
            cam.record_stop()
            state = cam.record_state() or {}
            logger.info(f"record/stop → {state}")
            return state

    @app.get("/api/format", dependencies=[Depends(_require_key)])
    def get_format() -> dict:
        with _camera() as cam:
            return cam.get_format()

    @app.patch("/api/format", dependencies=[Depends(_require_key)])
    def patch_format(patch: FormatPatch = Body(...)) -> dict:
        body: dict[str, Any] = {}
        if patch.codec is not None:
            body["codec"] = patch.codec
        if patch.frameRate is not None:
            body["frameRate"] = patch.frameRate
        if patch.width is not None or patch.height is not None:
            res: dict[str, int] = {}
            if patch.width is not None:
                res["width"] = patch.width
            if patch.height is not None:
                res["height"] = patch.height
            body["recordResolution"] = res
        if patch.offSpeedEnabled is not None:
            body["offSpeedEnabled"] = patch.offSpeedEnabled
        if patch.offSpeedFrameRate is not None:
            body["offSpeedFrameRate"] = patch.offSpeedFrameRate
        if not body:
            raise HTTPException(status_code=400, detail="no fields to patch")
        logger.info(f"format: patch {body}")
        with _camera() as cam:
            result = cam.set_format(**body)
        logger.info(f"format: applied → {result}")
        return result

    @app.get("/api/files", dependencies=[Depends(_require_key)])
    def get_files(limit: int = 0) -> list:
        with _camera() as cam:
            clips = cam.list_clips()
        if limit > 0:
            clips = clips[-limit:]
        return clips

    @app.get("/api/filename", dependencies=[Depends(_require_key)])
    def get_filename() -> dict:
        with _camera() as cam:
            name = cam.current_filename()
        return {"filename": name}

    @app.get("/api/storage", dependencies=[Depends(_require_key)])
    def get_storage() -> dict:
        with _camera() as cam:
            return {"active": cam.active_disk(), "workingset": cam.workingset()}

    @app.get("/api/video/{name}", dependencies=[Depends(_require_key)])
    def get_video(name: str) -> dict | None:
        try:
            with _camera() as cam:
                return cam.video_param(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.put("/api/video/{name}", dependencies=[Depends(_require_key)])
    async def put_video(name: str, request: Request) -> dict | None:
        body = await request.json()
        logger.info(f"video/{name}: set {body}")
        try:
            with _camera() as cam:
                return cam.set_video_param(name, body) or {"ok": True}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/mounts", dependencies=[Depends(_require_key)])
    def get_mounts() -> list:
        with _camera() as cam:
            return cam.list_mounts()

    @app.get("/api/mounts/{full_path:path}", dependencies=[Depends(_require_key)])
    def get_mount_listing(full_path: str) -> list:
        with _camera() as cam:
            try:
                return cam.list_mount(full_path)
            except NotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/download/{full_path:path}", dependencies=[Depends(_require_key)])
    def get_download(full_path: str) -> StreamingResponse:
        cam = _camera()
        try:
            resp = cam.download_stream(full_path)
        except NotFoundError as e:
            cam.close()
            raise HTTPException(status_code=404, detail=str(e)) from e

        def _iter():
            try:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                resp.close()
                cam.close()

        headers = {}
        clen = resp.headers.get("Content-Length")
        if clen:
            headers["Content-Length"] = clen
        name = full_path.rsplit("/", 1)[-1] or "download"
        headers["Content-Disposition"] = (
            f'attachment; filename="{urllib.parse.quote(name)}"'
        )
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        logger.info(f"download: {full_path} ({clen or '?'} bytes)")
        return StreamingResponse(_iter(), media_type=ctype, headers=headers)

    @app.get("/api/logs", dependencies=[Depends(_require_key)])
    def get_logs(limit: int = 200) -> list:
        buf = list(_log_ring.buf)
        if limit > 0:
            buf = buf[-limit:]
        return buf

    @app.websocket("/api/logs/ws")
    async def ws_logs(websocket) -> None:  # type: ignore[no-untyped-def]
        await websocket.accept()
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        for entry in list(_log_ring.buf)[-50:]:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                break
        _log_ring.subscribers.add(q)
        try:
            while True:
                entry = await q.get()
                await websocket.send_text(jsonlib.dumps(entry))
        except Exception:
            pass
        finally:
            _log_ring.subscribers.discard(q)
            try:
                await websocket.close()
            except Exception:
                pass

    def _log_event(event: dict) -> None:
        # Summarize camera events into a one-line log entry.
        d = event.get("data") if isinstance(event, dict) else None
        if not isinstance(d, dict):
            logger.info(f"event: {event}")
            return
        if event.get("type") == "event" and d.get("action") == "propertyValueChanged":
            prop = d.get("property")
            val = d.get("value")
            logger.info(f"camera: {prop} = {val}")
        elif event.get("type") == "response":
            act = d.get("action")
            ok = d.get("success")
            if act == "subscribe" and ok:
                props = d.get("properties") or []
                logger.info(f"camera: subscribed to {len(props)} properties")
            elif not ok:
                logger.warning(f"camera response: {d}")

    @app.websocket("/api/events")
    async def ws_events(websocket) -> None:  # type: ignore[no-untyped-def]
        await websocket.accept()
        try:
            for event in event_stream(
                settings.host,
                username=settings.username,
                password=settings.password,
            ):
                _log_event(event)
                await websocket.send_text(jsonlib.dumps(event, default=str))
        except CameraError as e:
            logger.error(f"event stream: {type(e).__name__}: {e}")
            await websocket.send_text(
                jsonlib.dumps({"error": type(e).__name__, "detail": str(e)})
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"event stream error: {e}")
            await websocket.send_text(
                jsonlib.dumps({"error": "stream_error", "detail": str(e)})
            )
        finally:
            await websocket.close()

    return app


def settings_from_env() -> Settings:
    origins_raw = os.environ.get("BMCAM_ALLOW_ORIGINS", "*")
    allow_origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    return Settings(
        host=os.environ.get("BMCAM_HOST", "192.168.0.194"),
        username=os.environ.get("BMCAM_USER"),
        password=os.environ.get("BMCAM_PASSWORD"),
        timeout=float(os.environ.get("BMCAM_TIMEOUT", "5.0")),
        api_key=os.environ.get("BMCAM_API_KEY"),
        allow_origins=allow_origins,
    )


# uvicorn auto-discovery: `uvicorn bmcam.server:app`
app = build_app(settings_from_env())

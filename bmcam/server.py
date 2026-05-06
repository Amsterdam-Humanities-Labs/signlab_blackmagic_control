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

from typing import Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


# --- request/response schemas ----------------------------------------------
# Codec + frame-rate enums come from the camera's own supportedFormats payload
# (see GET /api/supportedFormats for the live, per-resolution combinations).
# These cover every value the camera will accept on at least one resolution.

Codec = Literal[
    "BRaw:Q0", "BRaw:Q1", "BRaw:Q3", "BRaw:Q5",
    "BRaw:3_1", "BRaw:5_1", "BRaw:8_1", "BRaw:12_1",
]
FrameRate = Literal[
    "23.98", "24", "25", "29.97", "30", "50", "59.94", "60",
]


class FormatPatch(BaseModel):
    """Patch the camera's record format. All fields optional — only the
    fields you send are changed; unspecified fields are preserved.

    Note: not every (resolution, codec, fps) combo is valid. Call
    `GET /api/supportedFormats` to see what's allowed on this body.
    `6144x3456` (full open-gate) maxes at 50 fps.
    """
    codec: Codec | None = Field(
        default=None,
        description="BlackmagicRAW variant. `Q*` = constant quality, "
                    "`N_1` = constant bitrate (lower N = bigger files).",
        examples=["BRaw:8_1"],
    )
    frameRate: FrameRate | None = Field(
        default=None,
        description="Recording frame rate as a string.",
        examples=["50"],
    )
    width: int | None = Field(
        default=None,
        description="Record width in pixels. Combine with `height`. "
                    "Allowed: 3840, 5376, 6144 (consult supportedFormats).",
        examples=[6144],
    )
    height: int | None = Field(
        default=None,
        description="Record height in pixels.",
        examples=[3456],
    )
    offSpeedEnabled: bool | None = Field(
        default=None,
        description="Enable variable / off-speed frame rate (slow-mo).",
    )
    offSpeedFrameRate: float | None = Field(
        default=None,
        description="Off-speed capture rate. Range depends on codec/res; "
                    "typical 5..50 at 6K, up to 60 at lower res.",
        examples=[48.0],
    )


class RecordStartBody(BaseModel):
    """Optional body for `POST /api/record/start`."""
    clipName: str | None = Field(
        default=None,
        description="Filename for the next clip (without extension). "
                    "Letters, digits, `_`, `-`. Leave null/empty to use the "
                    "camera's auto-generated name like `1001_HHMMSS_C001.braw`.",
        examples=["MyScene_Take1"],
        max_length=60,
    )


class IsoBody(BaseModel):
    iso: int = Field(..., ge=100, le=25600, examples=[400],
                     description="ISO. Common stops: 100, 200, 400, 800, "
                                 "1250, 1600, 3200, 6400, 12800, 25600.")


class GainBody(BaseModel):
    gain: int = Field(..., ge=-12, le=36, examples=[0],
                      description="Gain in dB. Practical stops on this body: "
                                  "-12, -6, 0, 6, 12, 18, 24, 30, 36. "
                                  "(YAML nominal range -128..128.)")


class WhiteBalanceBody(BaseModel):
    whiteBalance: int = Field(..., ge=2500, le=10000, examples=[5600],
                              description="Color temperature in Kelvin.")


class WhiteBalanceTintBody(BaseModel):
    whiteBalanceTint: int = Field(..., ge=-50, le=50, examples=[10],
                                  description="Magenta(+) / green(−) tint.")


class ShutterBody(BaseModel):
    """Send exactly one of `shutterSpeed` or `shutterAngle`. If both are
    present, `shutterSpeed` wins (per the camera's YAML)."""
    shutterSpeed: int | None = Field(
        default=None, ge=1, le=50000, examples=[200],
        description="Denominator of 1/N s. Min equals sensor frame rate.")
    shutterAngle: int | None = Field(
        default=None, ge=100, le=36000, examples=[18000],
        description="Shutter angle × 100 (e.g. 18000 = 180°).")


class NdFilterBody(BaseModel):
    stop: float = Field(..., ge=0.0, le=15.0, examples=[2.0],
                        description="ND power in stops (this body has no "
                                    "internal ND but the endpoint exists).")


_VIDEO_BODY_MODELS = {
    "iso": IsoBody,
    "gain": GainBody,
    "whiteBalance": WhiteBalanceBody,
    "whiteBalanceTint": WhiteBalanceTintBody,
    "shutter": ShutterBody,
    "ndFilter": NdFilterBody,
}


VideoParamName = Literal[
    "iso", "shutter", "whiteBalance", "whiteBalanceTint", "gain", "ndFilter",
]


# --- response schemas ------------------------------------------------------

class Resolution(BaseModel):
    width: int = Field(
        ..., ge=1280, le=12288, examples=[6144],
        description="Pixels. On this body: 3840, 5376, or 6144.",
    )
    height: int = Field(
        ..., ge=720, le=8640, examples=[3456],
        description="Pixels. On this body: 2160, 2560, 3024, or 3456.",
    )


class VideoFormatResponse(BaseModel):
    """Shape of `GET /api/format`. Same shape returned after `PATCH`."""
    codec: Codec | None = Field(
        default=None, examples=["BRaw:8_1"],
        description="Active recording codec. One of the 8 BRaw variants.",
    )
    frameRate: FrameRate | None = Field(
        default=None, examples=["50"],
        description="Active frame rate (string). 6144x3456 max is '50'.",
    )
    recordResolution: Resolution | None = Field(
        default=None,
        description="Recording resolution. Allowed combinations are listed "
                    "by `GET /api/supportedFormats`.",
    )
    sensorResolution: Resolution | None = Field(
        default=None,
        description="Sensor crop driving the record resolution.",
    )
    offSpeedEnabled: bool | None = Field(
        default=None, examples=[False],
        description="When true, recording uses `offSpeedFrameRate` (slow-mo "
                    "or fast-mo) decoupled from the timeline frame rate.",
    )
    offSpeedFrameRate: float | None = Field(
        default=None, ge=1, le=120, examples=[48.0],
        description="Off-speed capture rate. On this body, valid range is "
                    "`minOffSpeedFrameRate`..`maxOffSpeedFrameRate` for the "
                    "current resolution/codec.",
    )
    minOffSpeedFrameRate: float | None = Field(
        default=None, ge=1, le=120, examples=[5],
        description="Lower bound for `offSpeedFrameRate` at current settings.",
    )
    maxOffSpeedFrameRate: float | None = Field(
        default=None, ge=1, le=120, examples=[50],
        description="Upper bound for `offSpeedFrameRate` at current settings.",
    )


class SupportedFormatEntry(BaseModel):
    recordResolution: Resolution
    sensorResolution: Resolution
    codecs: list[Codec] = Field(..., examples=[[
        "BRaw:Q0", "BRaw:Q1", "BRaw:Q3", "BRaw:Q5",
        "BRaw:3_1", "BRaw:5_1", "BRaw:8_1", "BRaw:12_1",
    ]])
    frameRates: list[FrameRate] = Field(..., examples=[[
        "23.98", "24", "25", "29.97", "30", "50", "59.94", "60",
    ]])
    minOffSpeedFrameRate: float | None = None
    maxOffSpeedFrameRate: float | None = None


class RecordStateResponse(BaseModel):
    """Shape of `GET /api/record`."""
    recording: bool = Field(..., examples=[False])


class TimecodeResponse(BaseModel):
    timecode: int | None = Field(
        default=None,
        description="Time-of-day timecode as BCD-packed integer "
                    "(0xHHMMSSFF — decode as hex digits → HH:MM:SS:FF).",
        examples=[0x13422510],
    )
    clip: int | None = Field(default=None, examples=[0x00010012])


class IsoResponse(BaseModel):
    iso: int = Field(
        ..., ge=100, le=25600, examples=[400],
        description="Sensor ISO. Practical stops: 100, 200, 400, 800, 1250, "
                    "1600, 3200, 6400, 12800, 25600 on the Studio 6K Pro. "
                    "(YAML nominal range is 0..2147483647 but the camera will "
                    "snap to its native sensitivities.)",
    )


class GainResponse(BaseModel):
    gain: int = Field(
        ..., ge=-12, le=36, examples=[0],
        description="Gain in dB. Practical stops on this body: -12, -6, 0, 6, "
                    "12, 18, 24, 30, 36. (YAML nominal range -128..128.)",
    )


class WhiteBalanceResponse(BaseModel):
    whiteBalance: int = Field(
        ..., ge=2500, le=10000, examples=[5600],
        description="Color temperature in Kelvin. Common presets: 3200 "
                    "(tungsten), 4000, 4500, 5600 (daylight), 6500.",
    )


class WhiteBalanceTintResponse(BaseModel):
    whiteBalanceTint: int = Field(
        ..., ge=-50, le=50, examples=[10],
        description="Magenta(+) / green(−) tint offset.",
    )


class ShutterResponse(BaseModel):
    continuousShutterAutoExposure: bool | None = Field(
        default=None, examples=[False],
        description="True if shutter is currently driven by auto-exposure.",
    )
    shutterSpeed: int | None = Field(
        default=None, ge=24, le=50000, examples=[200],
        description="Denominator of 1/N s. Min equals current sensor frame "
                    "rate; max 50000. Common: 48, 50, 60, 96, 100, 120, "
                    "200, 250, 500, 1000, 2000, 4000.",
    )
    shutterAngle: int | None = Field(
        default=None, ge=100, le=36000, examples=[18000],
        description="Shutter angle × 100 (e.g. 18000 = 180°). Range "
                    "100 (1°) .. 36000 (360°).",
    )


class NdFilterResponse(BaseModel):
    stop: float = Field(
        ..., ge=0.0, le=15.0, examples=[0.0],
        description="ND power in stops. Range 0..15. The Studio 6K Pro has "
                    "no internal ND, so this typically stays at 0.",
    )


class FilenameResponse(BaseModel):
    filename: str | None = Field(default=None, examples=["MyScene_Take1.braw"])


class DiskInfo(BaseModel):
    index: int = Field(..., examples=[0])
    deviceName: str = Field(..., examples=["usb4352"])
    volume: str | None = Field(default=None, examples=["UNTITLED"])
    activeDisk: bool = Field(..., examples=[True])
    clipCount: int = Field(..., examples=[14])
    totalSpace: int = Field(..., examples=[10000619536384],
                            description="Total bytes on this disk.")
    remainingSpace: int = Field(..., examples=[9509425905664],
                                description="Free bytes on this disk.")
    remainingRecordTime: int = Field(..., examples=[78538],
                                     description="Seconds at current codec/fps.")


class StorageResponse(BaseModel):
    active: dict | None = Field(
        default=None,
        description="Active disk descriptor: "
                    "`{deviceName, workingsetIndex}`.",
        examples=[{"deviceName": "usb4352", "workingsetIndex": 0}],
    )
    workingset: dict | list | None = Field(
        default=None,
        description="Either `{size, workingset:[DiskInfo,...]}` or a flat list, "
                    "depending on firmware. List members follow `DiskInfo`.",
    )


class ClipEntry(BaseModel):
    clipUniqueId: int | None = None
    fileName: str | None = Field(default=None, examples=["MyScene_Take1.braw"])
    duration: int | None = Field(default=None, examples=[5320],
                                 description="Duration in milliseconds.")
    frameCount: int | None = None


class MountEntry(BaseModel):
    name: str = Field(..., examples=["usb/UNTITLED"],
                      description="WMM-style mount path; combine with file "
                                  "names to build URLs.")
    type: str = Field(..., examples=["directory"])
    mtime: str | None = Field(
        default=None,
        description="HTTP-date-formatted modification time.",
        examples=["Wed, 22 Apr 2026 13:33:09"])


class FileEntry(BaseModel):
    name: str = Field(..., examples=["MyScene_Take1.braw"])
    type: Literal["file", "directory"] = Field(..., examples=["file"])
    size: int | None = Field(default=None, examples=[123664184],
                             description="Bytes (files only).")
    mtime: str | None = Field(default=None, examples=["Wed, 22 Apr 2026 13:33:09"])


class LogEntry(BaseModel):
    ts: float = Field(..., examples=[1776857135.97],
                      description="Unix epoch seconds.")
    level: str = Field(..., examples=["INFO"])
    logger: str = Field(..., examples=["bmcam"])
    message: str = Field(..., examples=["record/start → {'recording': True}"])


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    cameraHost: str = Field(..., examples=["192.168.0.194"])


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
        description=(
            "HTTP facade over a Blackmagic camera's REST API.\n\n"
            "**Format:** `PATCH /api/format` accepts `codec`, `frameRate`, "
            "`width`, `height`, `offSpeedEnabled`, `offSpeedFrameRate`. "
            "Not every combination is valid — call "
            "[`GET /api/supportedFormats`](#/format/get_supported_formats_api_supportedFormats_get) "
            "for the allowed (resolution × codec × fps) matrix on this body.\n\n"
            "**Video parameters:** `PUT /api/video/{name}` where name is one of "
            "`iso`, `shutter`, `whiteBalance`, `whiteBalanceTint`, `gain`, "
            "`ndFilter`. Body shape depends on `name` — see the "
            "model dropdown on the request body schema for each option.\n\n"
            "**Record:** `POST /api/record/start` optionally takes "
            "`{\"clipName\":\"...\"}` to name the next clip.\n\n"
            "**Files:** `GET /api/mounts` / `GET /api/mounts/{path}` to list, "
            "`GET /api/download/{path}` to stream-download a clip."
        ),
        version="0.2.0",
        openapi_tags=[
            {"name": "health", "description": "Liveness."},
            {"name": "record", "description": "Start/stop recording."},
            {"name": "format", "description": "Codec, fps, resolution."},
            {"name": "video",  "description": "ISO / shutter / WB / gain / ND."},
            {"name": "media",  "description": "Disks and clip index."},
            {"name": "files",  "description": "Browse and download captured clips."},
            {"name": "logs",   "description": "Server log ring buffer."},
            {"name": "events", "description": "Camera property-change websocket relay."},
        ],
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

    @app.get("/api/health", tags=["health"], summary="Liveness probe.",
             response_model=HealthResponse)
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

    @app.get("/api/status", tags=["media"],
             summary="Aggregate snapshot of format, record, video, media.",
             dependencies=[Depends(_require_key)])
    def get_status() -> dict:
        with _camera() as cam:
            return cam.status()

    @app.get("/api/record", tags=["record"],
             summary="Current recording state.",
             response_model=RecordStateResponse,
             dependencies=[Depends(_require_key)])
    def get_record() -> dict:
        with _camera() as cam:
            return cam.record_state() or {}

    @app.post("/api/record/start", tags=["record"],
              summary="Start recording (optionally with a clip name).",
              response_model=RecordStateResponse,
              dependencies=[Depends(_require_key)])
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

    @app.post("/api/record/stop", tags=["record"],
              summary="Stop recording.",
              response_model=RecordStateResponse,
              dependencies=[Depends(_require_key)])
    def post_record_stop() -> dict:
        with _camera() as cam:
            logger.info("record/stop requested")
            cam.record_stop()
            state = cam.record_state() or {}
            logger.info(f"record/stop → {state}")
            return state

    @app.get("/api/format", tags=["format"],
             summary="Current record format (codec/fps/resolution).",
             response_model=VideoFormatResponse,
             dependencies=[Depends(_require_key)])
    def get_format() -> dict:
        with _camera() as cam:
            return cam.get_format()

    @app.get("/api/supportedFormats", tags=["format"],
             summary="Allowed (resolution × codec × fps) matrix on this body.",
             response_model=list[SupportedFormatEntry],
             dependencies=[Depends(_require_key)])
    def get_supported_formats() -> list:
        with _camera() as cam:
            return cam.supported_formats()

    @app.patch("/api/format", tags=["format"],
               summary="Change codec / fps / resolution / off-speed.",
               response_model=VideoFormatResponse,
               dependencies=[Depends(_require_key)])
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

    @app.get("/api/files", tags=["media"],
             summary="Clip index from camera /timelines/0 (recorded takes).",
             response_model=list[ClipEntry],
             dependencies=[Depends(_require_key)])
    def get_files(limit: int = 0) -> list:
        with _camera() as cam:
            clips = cam.list_clips()
        if limit > 0:
            clips = clips[-limit:]
        return clips

    @app.get("/api/filename", tags=["media"],
             summary="Filename of the most recent clip.",
             response_model=FilenameResponse,
             dependencies=[Depends(_require_key)])
    def get_filename() -> dict:
        with _camera() as cam:
            name = cam.current_filename()
        return {"filename": name}

    @app.get("/api/storage", tags=["media"],
             summary="Active disk + workingset (capacity, free, rec time).",
             response_model=StorageResponse,
             dependencies=[Depends(_require_key)])
    def get_storage() -> dict:
        with _camera() as cam:
            return {"active": cam.active_disk(), "workingset": cam.workingset()}

    def _get_video(name: str) -> dict:
        try:
            with _camera() as cam:
                return cam.video_param(name) or {}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    def _set_video(name: str, payload: dict) -> dict:
        logger.info(f"video/{name}: set {payload}")
        try:
            with _camera() as cam:
                return cam.set_video_param(name, payload) or {"ok": True}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/api/video/iso", tags=["video"], summary="Read ISO.",
             response_model=IsoResponse, dependencies=[Depends(_require_key)])
    def get_iso() -> dict: return _get_video("iso")

    @app.put("/api/video/iso", tags=["video"], summary="Set ISO.",
             response_model=IsoResponse, dependencies=[Depends(_require_key)])
    def put_iso(body: IsoBody) -> dict:
        return _set_video("iso", body.model_dump(exclude_none=True))

    @app.get("/api/video/gain", tags=["video"], summary="Read gain (dB).",
             response_model=GainResponse, dependencies=[Depends(_require_key)])
    def get_gain() -> dict: return _get_video("gain")

    @app.put("/api/video/gain", tags=["video"], summary="Set gain (dB).",
             response_model=GainResponse, dependencies=[Depends(_require_key)])
    def put_gain(body: GainBody) -> dict:
        return _set_video("gain", body.model_dump(exclude_none=True))

    @app.get("/api/video/whiteBalance", tags=["video"],
             summary="Read white balance (Kelvin).",
             response_model=WhiteBalanceResponse,
             dependencies=[Depends(_require_key)])
    def get_wb() -> dict: return _get_video("whiteBalance")

    @app.put("/api/video/whiteBalance", tags=["video"],
             summary="Set white balance (Kelvin).",
             response_model=WhiteBalanceResponse,
             dependencies=[Depends(_require_key)])
    def put_wb(body: WhiteBalanceBody) -> dict:
        return _set_video("whiteBalance", body.model_dump(exclude_none=True))

    @app.get("/api/video/whiteBalanceTint", tags=["video"],
             summary="Read white-balance tint.",
             response_model=WhiteBalanceTintResponse,
             dependencies=[Depends(_require_key)])
    def get_wb_tint() -> dict: return _get_video("whiteBalanceTint")

    @app.put("/api/video/whiteBalanceTint", tags=["video"],
             summary="Set white-balance tint.",
             response_model=WhiteBalanceTintResponse,
             dependencies=[Depends(_require_key)])
    def put_wb_tint(body: WhiteBalanceTintBody) -> dict:
        return _set_video("whiteBalanceTint", body.model_dump(exclude_none=True))

    @app.get("/api/video/shutter", tags=["video"],
             summary="Read shutter (speed or angle).",
             response_model=ShutterResponse,
             dependencies=[Depends(_require_key)])
    def get_shutter() -> dict: return _get_video("shutter")

    @app.put("/api/video/shutter", tags=["video"],
             summary="Set shutter (speed or angle).",
             response_model=ShutterResponse,
             dependencies=[Depends(_require_key)])
    def put_shutter(body: ShutterBody) -> dict:
        payload = body.model_dump(exclude_none=True)
        if not payload:
            raise HTTPException(
                status_code=400,
                detail="provide one of shutterSpeed or shutterAngle",
            )
        return _set_video("shutter", payload)

    @app.get("/api/video/ndFilter", tags=["video"],
             summary="Read ND filter (stops).",
             response_model=NdFilterResponse,
             dependencies=[Depends(_require_key)])
    def get_nd() -> dict: return _get_video("ndFilter")

    @app.put("/api/video/ndFilter", tags=["video"],
             summary="Set ND filter (stops). Endpoint exists even on bodies "
                     "without internal ND.",
             response_model=NdFilterResponse,
             dependencies=[Depends(_require_key)])
    def put_nd(body: NdFilterBody) -> dict:
        return _set_video("ndFilter", body.model_dump(exclude_none=True))

    @app.get("/api/mounts", tags=["files"],
             summary="List mounted disks (Web Media Manager).",
             response_model=list[MountEntry],
             dependencies=[Depends(_require_key)])
    def get_mounts() -> list:
        with _camera() as cam:
            return cam.list_mounts()

    @app.get("/api/mounts/{full_path:path}", tags=["files"],
             summary="List a directory under a mount, e.g. `usb/UNTITLED`.",
             response_model=list[FileEntry],
             dependencies=[Depends(_require_key)])
    def get_mount_listing(full_path: str) -> list:
        with _camera() as cam:
            try:
                return cam.list_mount(full_path)
            except NotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/api/mounts/{full_path:path}", tags=["files"],
                summary="Delete a file or directory on the camera disk. "
                        "Irreversible — the camera writes through to the "
                        "USB/SD media immediately.",
                status_code=204,
                dependencies=[Depends(_require_key)])
    def delete_mount_path(full_path: str) -> None:
        logger.info(f"delete: {full_path}")
        with _camera() as cam:
            try:
                cam.delete_file(full_path)
            except NotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/download/{full_path:path}", tags=["files"],
             summary="Stream-download a file (e.g. a `.braw` clip).",
             dependencies=[Depends(_require_key)])
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

    @app.get("/api/logs", tags=["logs"],
             summary="Recent server log entries (in-memory ring buffer).",
             response_model=list[LogEntry],
             dependencies=[Depends(_require_key)])
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

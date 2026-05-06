from __future__ import annotations

from typing import Any

import requests

from .errors import (
    ApiDisabledError,
    BadRequestError,
    CameraError,
    NoMediaError,
    NotFoundError,
    UnreachableError,
)
from .models import Clip, Disk, RecordState, SystemInfo, Timecode, VideoFormat

_VIDEO_PARAMS = ("iso", "shutter", "whiteBalance", "whiteBalanceTint", "gain", "ndFilter")


class Camera:
    def __init__(
        self,
        host: str = "192.168.0.194",
        timeout: float = 5.0,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.host = host
        self.timeout = timeout
        self.base = f"http://{host}/control/api/v1"
        self._session = requests.Session()
        if username is not None and password is not None:
            self._session.auth = (username, password)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, json: Any | None = None) -> Any:
        url = f"{self.base}{path}"
        try:
            resp = self._session.request(method, url, json=json, timeout=self.timeout)
        except requests.ConnectionError as e:
            raise UnreachableError(f"cannot reach {self.host}: {e}", url=url) from e
        except requests.Timeout as e:
            raise UnreachableError(f"timeout talking to {self.host}", url=url) from e
        if resp.status_code == 404:
            raise NotFoundError(f"404 {path}", url=url, status=404, payload=resp.text)
        if resp.status_code == 501:
            raise NotFoundError(
                f"501 {path}: not implemented on this camera",
                url=url,
                status=501,
                payload=resp.text,
            )
        if 400 <= resp.status_code < 500:
            raise BadRequestError(
                f"HTTP {resp.status_code} {path}: {resp.text[:200]}",
                url=url,
                status=resp.status_code,
                payload=resp.text,
            )
        if resp.status_code >= 500:
            raise CameraError(
                f"HTTP {resp.status_code} {path}",
                url=url,
                status=resp.status_code,
                payload=resp.text,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return resp.json()
        return resp.text

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _put(self, path: str, json: Any) -> Any:
        return self._request("PUT", path, json=json)

    def _safe_get(self, path: str, default: Any = None) -> Any:
        try:
            return self._get(path)
        except NotFoundError:
            return default

    def system(self) -> SystemInfo:
        try:
            return self._get("/system")
        except NotFoundError as e:
            raise ApiDisabledError(
                "Camera REST API not serving /system — enable Web Media Manager and "
                "REST API on the camera (Setup → Network) and retry.",
                url=e.url,
                status=404,
            ) from e

    def get_format(self) -> VideoFormat:
        return self._get("/system/format")

    def supported_formats(self) -> list:
        data = self._safe_get("/system/supportedFormats", default={}) or {}
        if isinstance(data, dict):
            return data.get("supportedFormats") or []
        if isinstance(data, list):
            return data
        return []

    def set_format(self, **patch: Any) -> VideoFormat:
        current = self.get_format()
        merged = {**current, **patch}
        self._put("/system/format", merged)
        return self.get_format()

    def record_state(self) -> RecordState:
        return self._get("/transports/0/record")

    def record_start(self, clip_name: str | None = None) -> None:
        active = self.active_disk() or {}
        if not active or not active.get("deviceName"):
            raise NoMediaError(
                "no active recording media — insert/reconnect an SD card or USB "
                "drive and set it active in the camera."
            )
        ws = self.workingset() or []
        for d in ws:
            if d.get("activeDisk") and (d.get("remainingRecordTime") or 0) <= 0:
                raise NoMediaError(
                    "active disk has no remaining record time (disk full or "
                    "unsupported format)."
                )
        body: dict[str, Any] = {"recording": True}
        if clip_name:
            body["clipName"] = clip_name
        self._put("/transports/0/record", body)

    def record_stop(self) -> None:
        self._put("/transports/0/record", {"recording": False})

    def timecode(self) -> Timecode:
        return self._get("/transports/0/timecode")

    def list_clips(self) -> list[Clip]:
        data = self._safe_get("/timelines/0", default={}) or {}
        clips = data.get("clips") if isinstance(data, dict) else None
        return clips or []

    def current_filename(self) -> str | None:
        clips = self.list_clips()
        if not clips:
            return None
        last = clips[-1]
        return last.get("fileName") or last.get("filePath")

    def workingset(self) -> list[Disk]:
        data = self._safe_get("/media/workingset", default={}) or {}
        if isinstance(data, dict):
            disks = data.get("workingset") or data.get("disks") or []
            return disks
        if isinstance(data, list):
            return data
        return []

    def active_disk(self) -> Disk | None:
        return self._safe_get("/media/active")

    def video_param(self, name: str) -> Any:
        if name not in _VIDEO_PARAMS:
            raise ValueError(f"unknown video param: {name}")
        return self._safe_get(f"/video/{name}")

    def set_video_param(self, name: str, value: Any) -> Any:
        if name not in _VIDEO_PARAMS:
            raise ValueError(f"unknown video param: {name}")
        return self._put(f"/video/{name}", value)

    def mounts_url(self) -> str:
        return f"http://{self.host}/mounts"

    def list_mounts(self) -> list:
        url = f"{self.mounts_url()}/"
        resp = self._session.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise CameraError(
                f"HTTP {resp.status_code} GET /mounts/",
                url=url,
                status=resp.status_code,
                payload=resp.text,
            )
        try:
            return resp.json() or []
        except ValueError:
            return []

    def list_mount(self, path: str) -> list:
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path = path + "/"
        url = f"{self.mounts_url()}{path}"
        resp = self._session.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            raise NotFoundError(f"404 {path}", url=url, status=404, payload=resp.text)
        if resp.status_code >= 400:
            raise CameraError(
                f"HTTP {resp.status_code} GET {path}",
                url=url,
                status=resp.status_code,
                payload=resp.text,
            )
        try:
            return resp.json() or []
        except ValueError:
            return []

    def delete_file(self, path: str) -> None:
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.mounts_url()}{path}"
        resp = self._session.delete(url, timeout=self.timeout)
        if resp.status_code == 404:
            raise NotFoundError(f"404 {path}", url=url, status=404, payload=resp.text)
        if resp.status_code >= 400:
            raise CameraError(
                f"HTTP {resp.status_code} DELETE {path}",
                url=url,
                status=resp.status_code,
                payload=resp.text,
            )

    def download_stream(self, path: str) -> "requests.Response":
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.mounts_url()}{path}"
        resp = self._session.get(url, timeout=self.timeout, stream=True)
        if resp.status_code == 404:
            resp.close()
            raise NotFoundError(f"404 {path}", url=url, status=404)
        if resp.status_code >= 400:
            body = resp.text
            resp.close()
            raise CameraError(
                f"HTTP {resp.status_code} GET {path}",
                url=url,
                status=resp.status_code,
                payload=body,
            )
        return resp

    def status(self) -> dict:
        try:
            self._get("/system")
        except NotFoundError as e:
            raise ApiDisabledError(
                "Camera REST API not reachable at /system — enable Web Media Manager "
                "and REST API on the camera (Setup → Network) and retry.",
                url=e.url,
                status=e.status,
            ) from e
        out: dict = {"host": self.host}
        out["format"] = self._safe_get("/system/format")
        out["codecFormat"] = self._safe_get("/system/codecFormat")
        out["record"] = self._safe_get("/transports/0/record")
        out["timecode"] = self._safe_get("/transports/0/timecode")
        out["video"] = {p: self._safe_get(f"/video/{p}") for p in _VIDEO_PARAMS}
        out["media"] = {
            "active": self._safe_get("/media/active"),
            "workingset": self._safe_get("/media/workingset"),
        }
        out["lastClip"] = self.current_filename()
        return out

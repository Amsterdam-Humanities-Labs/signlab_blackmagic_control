from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from dataclasses import dataclass
from typing import Any

from bmcam import Camera


@dataclass
class Settings:
    camera_host: str = "192.168.0.194"
    camera_user: str | None = None
    camera_password: str | None = None
    camera_timeout: float = 5.0
    service_name: str = "BlackmagicCamera"
    service_type: str = "_mocap._tcp.local."
    bind: str = "0.0.0.0"
    port: int = 8780


@dataclass
class Command:
    type: str
    value: str | None = None


class BlackmagicAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clip_name: str | None = None
        self._lock = asyncio.Lock()

    async def handle(self, message: str) -> str:
        cmd = parse_command(message)
        if cmd.type == "SetName":
            self._clip_name = cmd.value or None
            return json.dumps({"type": "status", "msg": "name set", "value": self._clip_name})
        if cmd.type == "Start":
            await self._camera_call("record_start", clip_name=self._clip_name)
            return json.dumps({"type": "status", "msg": "recording started"})
        if cmd.type == "Stop":
            await self._camera_call("record_stop")
            return json.dumps({"type": "status", "msg": "recording stopped"})
        if cmd.type == "health":
            try:
                await self._camera_call("status")
                return "Good"
            except Exception:
                return "Bad"
        return json.dumps({"type": "error", "msg": f"unknown command: {cmd.type}"})

    async def _camera_call(self, method: str, **kwargs: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(self._call_camera, method, kwargs)

    def _call_camera(self, method: str, kwargs: dict[str, Any]) -> Any:
        with Camera(
            host=self.settings.camera_host,
            timeout=self.settings.camera_timeout,
            username=self.settings.camera_user,
            password=self.settings.camera_password,
        ) as cam:
            return getattr(cam, method)(**kwargs)


def parse_command(raw: str) -> Command:
    text = raw.strip()
    if not text:
        return Command("unknown")

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None

    if isinstance(obj, dict):
        typ = str(obj.get("type") or obj.get("command") or "").strip()
        value = obj.get("value") or obj.get("name") or obj.get("clipName")
        return normalize_command(typ, None if value is None else str(value))

    head, _, tail = text.partition(" ")
    return normalize_command(head, tail.strip() or None)


def normalize_command(kind: str, value: str | None = None) -> Command:
    aliases = {
        "start": "Start",
        "recordstart": "Start",
        "stop": "Stop",
        "recordstop": "Stop",
        "setname": "SetName",
        "filename": "SetName",
        "health": "health",
    }
    normalized = aliases.get(kind.replace("_", "").lower(), kind)
    return Command(normalized, value)


async def websocket_handler(websocket: Any, adapter: BlackmagicAdapter) -> None:
    async for message in websocket:
        try:
            reply = await adapter.handle(str(message))
        except Exception as exc:
            reply = json.dumps({"type": "error", "msg": str(exc)})
        await websocket.send(reply)


async def run(settings: Settings) -> None:
    import websockets

    zeroconf, info = register_zeroconf(settings)
    adapter = BlackmagicAdapter(settings)
    try:
        async with websockets.serve(
            lambda ws: websocket_handler(ws, adapter),
            settings.bind,
            settings.port,
        ):
            print(
                f"Blackmagic service listening on ws://{settings.bind}:{settings.port} "
                f"for camera {settings.camera_host}"
            )
            await asyncio.Future()
    finally:
        zeroconf.unregister_service(info)
        zeroconf.close()


def register_zeroconf(settings: Settings) -> tuple[Any, Any]:
    from zeroconf import ServiceInfo, Zeroconf

    service_type = ensure_dot(settings.service_type)
    service_name = f"{settings.service_name}.{service_type}"
    ip = local_ip()
    info = ServiceInfo(
        service_type,
        service_name,
        addresses=[socket.inet_aton(ip)],
        port=settings.port,
        properties={"camera": settings.camera_host},
        server=f"{socket.gethostname()}.local.",
    )
    zeroconf = Zeroconf()
    zeroconf.register_service(info)
    print(f"Registered Zeroconf service {service_name} at {ip}:{settings.port}")
    return zeroconf, info


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def ensure_dot(value: str) -> str:
    return value if value.endswith(".") else value + "."


def load_settings(path: str | None) -> Settings:
    data: dict[str, Any] = {}
    if path:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    env = os.environ
    merged = {
        "camera_host": env.get("BMCAM_HOST", data.get("camera_host", Settings.camera_host)),
        "camera_user": env.get("BMCAM_USER", data.get("camera_user")),
        "camera_password": env.get("BMCAM_PASSWORD", data.get("camera_password")),
        "camera_timeout": float(env.get("BMCAM_TIMEOUT", data.get("camera_timeout", Settings.camera_timeout))),
        "service_name": env.get("BMCAM_SERVICE_NAME", data.get("service_name", Settings.service_name)),
        "service_type": env.get("BMCAM_SERVICE_TYPE", data.get("service_type", Settings.service_type)),
        "bind": env.get("BMCAM_SERVICE_BIND", data.get("bind", Settings.bind)),
        "port": int(env.get("BMCAM_SERVICE_PORT", data.get("port", Settings.port))),
    }
    return Settings(**merged)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blackmagic adapter for Pineapple discovery pipeline")
    parser.add_argument("--config", help="Path to a YAML config file")
    args = parser.parse_args()
    asyncio.run(run(load_settings(args.config)))


if __name__ == "__main__":
    main()


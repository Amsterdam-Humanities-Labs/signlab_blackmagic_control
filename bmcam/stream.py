from __future__ import annotations

import json as jsonlib
import os
import shutil
import signal
import subprocess
import sys
from typing import Iterator

import requests
import websocket

from .errors import UnreachableError


def event_stream(
    host: str,
    username: str | None = None,
    password: str | None = None,
) -> Iterator[dict]:
    url = f"ws://{host}/control/api/v1/event/websocket"
    header = []
    if username is not None and password is not None:
        import base64

        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        header.append(f"Authorization: Basic {token}")
    ws = websocket.create_connection(url, timeout=10, header=header)
    try:
        ws.send(
            jsonlib.dumps(
                {
                    "type": "request",
                    "data": {
                        "action": "subscribe",
                        "properties": [
                            "/transports/0/record",
                            "/transports/0/timecode",
                            "/system/format",
                            "/video/iso",
                            "/video/shutter",
                            "/video/whiteBalance",
                            "/video/gain",
                            "/video/ndFilter",
                            "/media/active",
                        ],
                    },
                }
            )
        )
        while True:
            msg = ws.recv()
            if not msg:
                break
            try:
                yield jsonlib.loads(msg)
            except jsonlib.JSONDecodeError:
                yield {"raw": msg}
    finally:
        ws.close()


def run_event_stream(
    host: str,
    as_json: bool = True,
    username: str | None = None,
    password: str | None = None,
) -> None:
    def _sigint(_sig: int, _frame: object) -> None:
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    try:
        for event in event_stream(host, username=username, password=password):
            if as_json:
                sys.stdout.write(jsonlib.dumps(event, default=str) + "\n")
            else:
                sys.stdout.write(f"{event}\n")
            sys.stdout.flush()
    except (websocket.WebSocketException, ConnectionError, OSError) as e:
        raise UnreachableError(f"websocket error: {e}") from e


def _probe_preview(host: str) -> tuple[str, str] | None:
    for path in ("/control/api/v1/preview", "/control/api/v1/preview/stream.m3u8"):
        url = f"http://{host}{path}"
        try:
            resp = requests.get(url, stream=True, timeout=5)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            ctype = resp.headers.get("Content-Type", "")
            resp.close()
            return url, ctype
        resp.close()
    return None


def run_preview(host: str) -> None:
    found = _probe_preview(host)
    if found is None:
        sys.stderr.write(
            "no network video preview endpoint on this camera. "
            "Most Pocket Cinema Camera bodies output preview only on HDMI/SDI.\n"
        )
        sys.exit(2)
    url, ctype = found
    player = os.environ.get("BMCAM_PLAYER")
    if player is None:
        for candidate in ("ffplay", "vlc", "mpv"):
            if shutil.which(candidate):
                player = candidate
                break
    if player is None:
        sys.stderr.write(
            f"preview available at {url} ({ctype}) but no player found. "
            "Install ffmpeg/vlc/mpv or set BMCAM_PLAYER.\n"
        )
        sys.exit(3)
    sys.stderr.write(f"opening {url} with {player}\n")
    subprocess.run([player, url], check=False)

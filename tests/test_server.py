from __future__ import annotations

import responses
from fastapi.testclient import TestClient

from bmcam.server import Settings, build_app

BASE = "http://192.168.0.194/control/api/v1"


def _client(api_key: str | None = None) -> TestClient:
    settings = Settings(
        host="192.168.0.194",
        username=None,
        password=None,
        timeout=1.0,
        api_key=api_key,
        allow_origins=["*"],
    )
    app = build_app(settings)
    return TestClient(app)


def test_health() -> None:
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json()["cameraHost"] == "192.168.0.194"


@responses.activate
def test_get_record() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/transports/0/record",
        json={"recording": False},
        status=200,
    )
    r = _client().get("/api/record")
    assert r.status_code == 200
    assert r.json() == {"recording": False}


@responses.activate
def test_post_record_start() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/media/active",
        json={"deviceName": "sd1", "workingsetIndex": 0},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/media/workingset",
        json={"workingset": [
            {"deviceName": "sd1", "activeDisk": True, "remainingRecordTime": 1000},
        ]},
        status=200,
    )
    responses.add(responses.PUT, f"{BASE}/transports/0/record", status=200, json={})
    responses.add(
        responses.GET,
        f"{BASE}/transports/0/record",
        json={"recording": True},
        status=200,
    )
    r = _client().post("/api/record/start")
    assert r.status_code == 200
    assert r.json() == {"recording": True}


@responses.activate
def test_post_record_start_without_media_returns_409() -> None:
    responses.add(responses.GET, f"{BASE}/media/active", status=204)
    r = _client().post("/api/record/start")
    assert r.status_code == 409
    assert r.json()["error"] == "no_media"


@responses.activate
def test_post_record_stop() -> None:
    responses.add(responses.PUT, f"{BASE}/transports/0/record", status=200, json={})
    responses.add(
        responses.GET,
        f"{BASE}/transports/0/record",
        json={"recording": False},
        status=200,
    )
    r = _client().post("/api/record/stop")
    assert r.status_code == 200
    assert responses.calls[0].request.body == b'{"recording": false}'


@responses.activate
def test_patch_format() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={"codec": "BRaw:8_1", "frameRate": "50"},
        status=200,
    )
    responses.add(responses.PUT, f"{BASE}/system/format", status=200, json={})
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={"codec": "BRaw:8_1", "frameRate": "25"},
        status=200,
    )
    r = _client().patch("/api/format", json={"frameRate": "25"})
    assert r.status_code == 200
    assert r.json()["frameRate"] == "25"


def test_patch_format_empty_rejected() -> None:
    r = _client().patch("/api/format", json={})
    assert r.status_code == 400


@responses.activate
def test_api_key_required_when_set() -> None:
    c = _client(api_key="secret")
    responses.add(
        responses.GET,
        f"{BASE}/transports/0/record",
        json={"recording": False},
        status=200,
    )
    assert c.get("/api/record").status_code == 401
    assert c.get("/api/record", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/api/record", headers={"X-API-Key": "secret"}).status_code == 200


@responses.activate
def test_api_disabled_returns_503() -> None:
    from bmcam.client import _VIDEO_PARAMS

    responses.add(responses.GET, f"{BASE}/system", status=404)
    r = _client().get("/api/status")
    assert r.status_code == 503
    assert r.json()["error"] == "api_disabled"


@responses.activate
def test_get_storage() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/media/active",
        json={"deviceName": "sd1"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/media/workingset",
        json={"workingset": [{"deviceName": "sd1"}]},
        status=200,
    )
    r = _client().get("/api/storage")
    assert r.status_code == 200
    data = r.json()
    assert data["active"] == {"deviceName": "sd1"}
    assert len(data["workingset"]) == 1


@responses.activate
def test_get_video_param() -> None:
    responses.add(responses.GET, f"{BASE}/video/iso", json={"iso": 800}, status=200)
    r = _client().get("/api/video/iso")
    assert r.status_code == 200
    assert r.json() == {"iso": 800}


def test_video_param_invalid_name() -> None:
    # /api/video/{name} is now per-param explicit routes, so unknown names 404.
    r = _client().get("/api/video/bogus")
    assert r.status_code == 404


@responses.activate
def test_delete_file_proxies_to_wmm() -> None:
    responses.add(
        responses.DELETE,
        "http://192.168.0.194/mounts/usb/UNTITLED/foo.braw",
        status=204,
    )
    r = _client().delete("/api/mounts/usb/UNTITLED/foo.braw")
    assert r.status_code == 204


@responses.activate
def test_delete_file_404_propagates() -> None:
    responses.add(
        responses.DELETE,
        "http://192.168.0.194/mounts/usb/UNTITLED/missing.braw",
        status=404,
    )
    r = _client().delete("/api/mounts/usb/UNTITLED/missing.braw")
    assert r.status_code == 404


def test_cors_origin_header_present() -> None:
    r = _client().get(
        "/api/health",
        headers={"Origin": "http://example.com"},
    )
    assert r.headers.get("access-control-allow-origin") in {"*", "http://example.com"}

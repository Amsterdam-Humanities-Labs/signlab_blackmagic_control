from __future__ import annotations

import pytest
import responses

from bmcam import ApiDisabledError, Camera, NotFoundError
from bmcam.client import _VIDEO_PARAMS

BASE = "http://192.168.0.194/control/api/v1"


@pytest.fixture
def cam() -> Camera:
    return Camera(host="192.168.0.194", timeout=1.0)


@responses.activate
def test_system_ok(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/system", json={"model": "6K Pro"}, status=200)
    assert cam.system() == {"model": "6K Pro"}


@responses.activate
def test_system_404_raises_api_disabled(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/system", status=404)
    with pytest.raises(ApiDisabledError):
        cam.system()


def _add_media_ok() -> None:
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


@responses.activate
def test_record_start(cam: Camera) -> None:
    _add_media_ok()
    responses.add(responses.PUT, f"{BASE}/transports/0/record", status=200, json={})
    cam.record_start()
    assert responses.calls[-1].request.body == b'{"recording": true}'


@responses.activate
def test_record_start_without_media_raises(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/media/active", status=204)
    from bmcam.errors import NoMediaError
    with pytest.raises(NoMediaError):
        cam.record_start()


@responses.activate
def test_record_stop(cam: Camera) -> None:
    responses.add(responses.PUT, f"{BASE}/transports/0/record", status=200, json={})
    cam.record_stop()
    assert responses.calls[0].request.body == b'{"recording": false}'


@responses.activate
def test_set_format_merges_and_reads_back(cam: Camera) -> None:
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={
            "codec": "BlackmagicRAW:Q5",
            "frameRate": "23.976",
            "recordResolution": {"width": 6144, "height": 3456},
        },
        status=200,
    )
    responses.add(responses.PUT, f"{BASE}/system/format", status=200, json={})
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={
            "codec": "BlackmagicRAW:Q5",
            "frameRate": "50",
            "recordResolution": {"width": 6144, "height": 3456},
        },
        status=200,
    )
    new = cam.set_format(frameRate="50")
    assert new["frameRate"] == "50"
    put_req = responses.calls[1].request
    assert b'"frameRate": "50"' in put_req.body


@responses.activate
def test_status_degrades_on_404(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/system", status=204)
    responses.add(responses.GET, f"{BASE}/system/format", json={"codec": "H.264"}, status=200)
    responses.add(responses.GET, f"{BASE}/system/codecFormat", status=501)
    responses.add(responses.GET, f"{BASE}/transports/0/record", status=404)
    responses.add(responses.GET, f"{BASE}/transports/0/timecode", status=404)
    for p in _VIDEO_PARAMS:
        responses.add(responses.GET, f"{BASE}/video/{p}", status=404)
    responses.add(responses.GET, f"{BASE}/media/active", status=404)
    responses.add(responses.GET, f"{BASE}/media/workingset", status=404)
    responses.add(responses.GET, f"{BASE}/timelines/0", status=404)

    st = cam.status()
    assert st["format"] == {"codec": "H.264"}
    assert st["codecFormat"] is None
    assert st["record"] is None
    assert st["timecode"] is None
    assert st["video"] == {p: None for p in _VIDEO_PARAMS}
    assert st["lastClip"] is None


@responses.activate
def test_status_api_disabled_when_system_404(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/system", status=404)
    with pytest.raises(ApiDisabledError):
        cam.status()


@responses.activate
def test_list_clips_from_timelines(cam: Camera) -> None:
    responses.add(
        responses.GET,
        f"{BASE}/timelines/0",
        json={"clips": [{"fileName": "A001.braw"}, {"fileName": "A002.braw"}]},
        status=200,
    )
    assert cam.list_clips() == [{"fileName": "A001.braw"}, {"fileName": "A002.braw"}]


@responses.activate
def test_current_filename_returns_last(cam: Camera) -> None:
    responses.add(
        responses.GET,
        f"{BASE}/timelines/0",
        json={"clips": [{"fileName": "A001.braw"}, {"fileName": "A002.braw"}]},
        status=200,
    )
    assert cam.current_filename() == "A002.braw"


@responses.activate
def test_workingset_and_active(cam: Camera) -> None:
    responses.add(
        responses.GET,
        f"{BASE}/media/workingset",
        json={"workingset": [{"deviceName": "sd1"}, {"deviceName": "usb0"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/media/active",
        json={"deviceName": "sd1"},
        status=200,
    )
    disks = cam.workingset()
    active = cam.active_disk()
    assert len(disks) == 2
    assert active == {"deviceName": "sd1"}


@responses.activate
def test_bad_request_surfaces_message(cam: Camera) -> None:
    responses.add(
        responses.PUT,
        f"{BASE}/system/format",
        status=400,
        body="invalid frame rate",
    )
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={"frameRate": "24"},
        status=200,
    )
    from bmcam.errors import BadRequestError

    with pytest.raises(BadRequestError) as exc:
        cam.set_format(frameRate="nonsense")
    assert "invalid frame rate" in str(exc.value)


@responses.activate
def test_video_param_get_set(cam: Camera) -> None:
    responses.add(responses.GET, f"{BASE}/video/iso", json={"iso": 400}, status=200)
    responses.add(responses.PUT, f"{BASE}/video/iso", status=200, json={})
    assert cam.video_param("iso") == {"iso": 400}
    cam.set_video_param("iso", {"iso": 800})
    assert b'"iso": 800' in responses.calls[1].request.body


def test_video_param_rejects_unknown(cam: Camera) -> None:
    with pytest.raises(ValueError):
        cam.video_param("nope")

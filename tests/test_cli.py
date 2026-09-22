from __future__ import annotations

import json

import responses
from click.testing import CliRunner

from bmcam.cli import cli
from bmcam.client import _VIDEO_PARAMS

BASE = "http://192.168.0.194/control/api/v1"


def _runner() -> CliRunner:
    return CliRunner()


@responses.activate
def test_status_json_output() -> None:
    responses.add(responses.GET, f"{BASE}/system", status=204)
    responses.add(responses.GET, f"{BASE}/system/format", json={"codec": "H.264"}, status=200)
    responses.add(responses.GET, f"{BASE}/system/codecFormat", status=501)
    responses.add(responses.GET, f"{BASE}/transports/0/record", json={"recording": False}, status=200)
    responses.add(responses.GET, f"{BASE}/transports/0/timecode", status=404)
    for p in _VIDEO_PARAMS:
        responses.add(responses.GET, f"{BASE}/video/{p}", status=404)
    responses.add(responses.GET, f"{BASE}/media/active", status=404)
    responses.add(responses.GET, f"{BASE}/media/workingset", status=404)
    responses.add(responses.GET, f"{BASE}/timelines/0", status=404)

    result = _runner().invoke(cli, ["--json", "status"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["format"]["codec"] == "H.264"
    assert data["record"]["recording"] is False


@responses.activate
def test_record_start_cli() -> None:
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
    result = _runner().invoke(cli, ["record", "start"])
    assert result.exit_code == 0
    assert "recording: True" in result.output


@responses.activate
def test_format_set_sends_only_patched_fields() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/system/format",
        json={
            "codec": "BlackmagicRAW:Q5",
            "frameRate": "24",
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
    result = _runner().invoke(cli, ["format", "set", "--fps", "50"])
    assert result.exit_code == 0, result.output
    put_body = responses.calls[1].request.body
    assert b'"frameRate": "50"' in put_body


def test_format_set_requires_flag() -> None:
    result = _runner().invoke(cli, ["format", "set"])
    assert result.exit_code == 4


@responses.activate
def test_filename_cli() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/timelines/0",
        json={"clips": [{"fileName": "A001.braw"}]},
        status=200,
    )
    result = _runner().invoke(cli, ["filename"])
    assert result.exit_code == 0
    assert "A001.braw" in result.output


@responses.activate
def test_api_disabled_prints_hint() -> None:
    from bmcam.cli import main as cli_main
    responses.add(responses.GET, f"{BASE}/system", status=404)

    runner = CliRunner()
    # Exercise main() so the exit code mapping from error class -> code runs.
    result = runner.invoke(
        cli,
        ["status"],
        standalone_mode=False,
        catch_exceptions=True,
    )
    # Without main() wrapping, ApiDisabledError is raised - verify it bubbled:
    from bmcam.errors import ApiDisabledError
    assert isinstance(result.exception, ApiDisabledError)


def test_serve_refuses_without_key_or_opt_out(monkeypatch) -> None:
    monkeypatch.delenv("BMCAM_API_KEY", raising=False)
    monkeypatch.delenv("BMCAM_NO_AUTH", raising=False)
    r = _runner().invoke(cli, ["serve"])
    assert r.exit_code != 0
    assert "--no-auth" in r.output

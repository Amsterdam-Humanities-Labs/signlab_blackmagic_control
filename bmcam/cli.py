from __future__ import annotations

import json as jsonlib
import os
import sys
from typing import Any

import click

from .client import Camera
from .errors import (
    ApiDisabledError,
    BadRequestError,
    CameraError,
    NotFoundError,
    UnreachableError,
)

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_API_DISABLED = 2
EXIT_NETWORK = 3
EXIT_BAD_ARGS = 4


def _emit(ctx: click.Context, data: Any, human: str | None = None) -> None:
    if ctx.obj.get("json"):
        click.echo(jsonlib.dumps(data, indent=2, default=str))
    else:
        if human is not None:
            click.echo(human)
        else:
            click.echo(_fmt(data))


def _fmt(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if data is None:
        return f"{pad}(none)"
    if isinstance(data, dict):
        return "\n".join(f"{pad}{k}: {_fmt_inline(v)}" for k, v in data.items())
    if isinstance(data, list):
        return "\n".join(f"{pad}- {_fmt_inline(v)}" for v in data)
    return f"{pad}{data}"


def _fmt_inline(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return jsonlib.dumps(v, default=str)
    return str(v)


@click.group()
@click.option("--host", envvar="BMCAM_HOST", default="192.168.0.194", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.option("--timeout", type=float, default=5.0, show_default=True)
@click.option("--user", "username", envvar="BMCAM_USER", default=None)
@click.option("--password", envvar="BMCAM_PASSWORD", default=None)
@click.pass_context
def cli(
    ctx: click.Context,
    host: str,
    as_json: bool,
    timeout: float,
    username: str | None,
    password: str | None,
) -> None:
    """Control a Blackmagic camera over the Camera Control REST API."""
    ctx.ensure_object(dict)
    ctx.obj["host"] = host
    ctx.obj["json"] = as_json
    ctx.obj["timeout"] = timeout
    ctx.obj["username"] = username
    ctx.obj["password"] = password


def _camera(ctx: click.Context) -> Camera:
    return Camera(
        host=ctx.obj["host"],
        timeout=ctx.obj["timeout"],
        username=ctx.obj.get("username"),
        password=ctx.obj.get("password"),
    )


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Get aggregate camera + video + media status."""
    with _camera(ctx) as cam:
        data = cam.status()
    _emit(ctx, data)


@cli.group()
def record() -> None:
    """Recording control."""


@record.command("start")
@click.option("--name", "clip_name", default=None,
              help="Name for this capture scene (becomes the clip filename).")
@click.pass_context
def record_start(ctx: click.Context, clip_name: str | None) -> None:
    with _camera(ctx) as cam:
        cam.record_start(clip_name=clip_name)
        state = cam.record_state()
    _emit(ctx, state, human=f"recording: {state.get('recording')}")


@record.command("stop")
@click.pass_context
def record_stop(ctx: click.Context) -> None:
    with _camera(ctx) as cam:
        cam.record_stop()
        state = cam.record_state()
    _emit(ctx, state, human=f"recording: {state.get('recording')}")


@record.command("state")
@click.pass_context
def record_state(ctx: click.Context) -> None:
    with _camera(ctx) as cam:
        state = cam.record_state()
    _emit(ctx, state)


@cli.group("format")
def fmt_grp() -> None:
    """Video format get/set."""


@fmt_grp.command("get")
@click.pass_context
def format_get(ctx: click.Context) -> None:
    with _camera(ctx) as cam:
        data = cam.get_format()
    _emit(ctx, data)


@fmt_grp.command("set")
@click.option("--width", type=int)
@click.option("--height", type=int)
@click.option("--fps", "frame_rate", type=str, help="e.g. 23.976, 24, 25, 29.97, 30, 50, 59.94, 60")
@click.option("--codec", type=str)
@click.pass_context
def format_set(
    ctx: click.Context,
    width: int | None,
    height: int | None,
    frame_rate: str | None,
    codec: str | None,
) -> None:
    patch: dict[str, Any] = {}
    if frame_rate is not None:
        patch["frameRate"] = frame_rate
    if codec is not None:
        patch["codec"] = codec
    if width is not None or height is not None:
        res: dict[str, int] = {}
        if width is not None:
            res["width"] = width
        if height is not None:
            res["height"] = height
        patch["recordResolution"] = res
    if not patch:
        click.echo("nothing to change; pass --width/--height/--fps/--codec", err=True)
        sys.exit(EXIT_BAD_ARGS)
    with _camera(ctx) as cam:
        new = cam.set_format(**patch)
    _emit(ctx, new)


@cli.command()
@click.option("--limit", type=int, default=0, help="Show at most N clips (0 = all)")
@click.pass_context
def files(ctx: click.Context, limit: int) -> None:
    """List clips on the active disk."""
    with _camera(ctx) as cam:
        clips = cam.list_clips()
    if limit > 0:
        clips = clips[-limit:]
    _emit(ctx, clips)


@cli.command()
@click.pass_context
def filename(ctx: click.Context) -> None:
    """Print the filename of the most recent clip."""
    with _camera(ctx) as cam:
        name = cam.current_filename()
    _emit(ctx, name, human=name or "(no clips)")


@cli.command()
@click.pass_context
def storage(ctx: click.Context) -> None:
    """Get working set + active disk."""
    with _camera(ctx) as cam:
        data = {"active": cam.active_disk(), "workingset": cam.workingset()}
    _emit(ctx, data)


@cli.command()
@click.option("--events", "mode", flag_value="events", default=True)
@click.option("--preview", "mode", flag_value="preview")
@click.pass_context
def stream(ctx: click.Context, mode: str) -> None:
    """Stream camera events (websocket) or probe for a video preview."""
    from . import stream as stream_mod

    host = ctx.obj["host"]
    if mode == "events":
        stream_mod.run_event_stream(
            host,
            as_json=ctx.obj["json"],
            username=ctx.obj.get("username"),
            password=ctx.obj.get("password"),
        )
    else:
        stream_mod.run_preview(host)


@cli.command()
@click.option("--bind", default="0.0.0.0", show_default=True, help="Interface to bind")
@click.option("--port", type=int, default=8000, show_default=True)
@click.option(
    "--api-key",
    envvar="BMCAM_API_KEY",
    default=None,
    help="Require X-API-Key header matching this value. If unset, API is open on the bound interface.",
)
@click.option(
    "--allow-origin",
    "allow_origins",
    multiple=True,
    default=("*",),
    help="CORS allowed origin (repeatable). Default *",
)
@click.option("--reload", is_flag=True, help="Auto-reload on code changes (dev only)")
@click.pass_context
def serve(
    ctx: click.Context,
    bind: str,
    port: int,
    api_key: str | None,
    allow_origins: tuple[str, ...],
    reload: bool,
) -> None:
    """Run a FastAPI HTTP server wrapping the camera."""
    import uvicorn

    os.environ["BMCAM_HOST"] = ctx.obj["host"]
    os.environ["BMCAM_TIMEOUT"] = str(ctx.obj["timeout"])
    if ctx.obj.get("username") is not None:
        os.environ["BMCAM_USER"] = ctx.obj["username"]
    if ctx.obj.get("password") is not None:
        os.environ["BMCAM_PASSWORD"] = ctx.obj["password"]
    if api_key is not None:
        os.environ["BMCAM_API_KEY"] = api_key
    os.environ["BMCAM_ALLOW_ORIGINS"] = ",".join(allow_origins)

    click.echo(
        f"serving bmcam API for {ctx.obj['host']} on http://{bind}:{port}  "
        f"(docs: http://{bind}:{port}/docs)",
        err=True,
    )
    uvicorn.run(
        "bmcam.server:app",
        host=bind,
        port=port,
        reload=reload,
        log_level="info",
    )


def main() -> None:
    """Script entry point used by `bmcam` console command."""
    try:
        cli(standalone_mode=False)
    except click.exceptions.UsageError as e:
        click.echo(e.format_message(), err=True)
        sys.exit(EXIT_BAD_ARGS)
    except click.exceptions.Exit as e:
        sys.exit(e.exit_code)
    except ApiDisabledError as e:
        click.echo(f"error: {e}", err=True)
        click.echo(
            "hint: on the camera, enable Setup → Network → Web Media Manager + REST API, "
            "then retry.",
            err=True,
        )
        sys.exit(EXIT_API_DISABLED)
    except NotFoundError as e:
        click.echo(f"endpoint not found: {e}", err=True)
        sys.exit(EXIT_API_DISABLED)
    except UnreachableError as e:
        click.echo(f"network error: {e}", err=True)
        sys.exit(EXIT_NETWORK)
    except BadRequestError as e:
        click.echo(f"bad request: {e}", err=True)
        sys.exit(EXIT_BAD_ARGS)
    except CameraError as e:
        click.echo(f"camera error: {e}", err=True)
        sys.exit(EXIT_GENERIC)
    except Exception as e:
        if os.environ.get("BMCAM_DEBUG"):
            raise
        click.echo(f"error: {e}", err=True)
        sys.exit(EXIT_GENERIC)


if __name__ == "__main__":  # pragma: no cover
    main()

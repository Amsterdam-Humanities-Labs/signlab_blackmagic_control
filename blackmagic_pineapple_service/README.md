# Blackmagic Pineapple service

Minimal websocket/Zeroconf adapter for the Pineapple discovery pipeline.

It advertises a `_mocap._tcp.local.` service, accepts the same simple websocket
commands used by the OBS/Shogun adapters, and forwards them to the Blackmagic
Camera REST API through `bmcam.Camera`.

## Install

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\blackmagic_pineapple_service\requirements.txt
```

## Run

Copy `config.example.yaml` if you want local settings, then run:

```powershell
.\.venv\Scripts\python.exe .\blackmagic_pineapple_service\service.py --config .\blackmagic_pineapple_service\config.example.yaml
```

Environment variables override config values:

```powershell
$env:BMCAM_HOST = "192.168.0.194"
$env:BMCAM_USER = "vislab"
$env:BMCAM_PASSWORD = "<camera password>"
$env:BMCAM_SERVICE_PORT = "8780"
.\.venv\Scripts\python.exe .\blackmagic_pineapple_service\service.py
```

## Commands

The service expects one websocket message per command:

```text
SetName TAKE_001
Start
Stop
health
```

`SetName` stores the next clip name. `Start` passes that stored value to:

```python
cam.record_start(clip_name="TAKE_001")
```

If no name has been set, `Start` lets the camera use its default clip name.
`health` returns `Good` when the camera REST API responds.

## Pineapple config

Copy `PineappleBlackmagicInterface.py` into the Pineapple pipeline repo:

```text
pineapplediscoverypipeline/scripts/BlackmagicInterface.py
```

Add a device entry to `pineapplediscoverypipeline/config.yaml`:

```yaml
- hostname: BlackmagicCamera._mocap._tcp.local
  attached_name: Blackmagic Camera
  script: "scripts/BlackmagicInterface.py"
```

The included Pineapple-side script maps:

```text
recordStart -> Start
recordStop  -> Stop
fileName    -> SetName <value>
health      -> health
```

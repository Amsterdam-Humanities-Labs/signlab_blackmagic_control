# Camera API documentation — local snapshot

Saved 2026-04-22 from the camera at `192.168.0.194`.

## How to refresh

```bash
curl -u vislab:"$BMCAM_PASSWORD" http://192.168.0.194/control/documentation.html
# then pull each YAML listed in the index:
curl -u vislab:"$BMCAM_PASSWORD" http://192.168.0.194/control/swagger/common/SystemControl.yaml
# etc.
```

Or open `http://192.168.0.194/control/documentation.html` in a browser.

## Files in this directory

| File | Source |
|---|---|
| `documentation.html` | `/control/documentation.html` — the index page |
| `EventControl.yaml` | `/control/swagger/common/EventControl.yaml` |
| `TransportControl.yaml` | `/control/swagger/common/TransportControl.yaml` |
| `MediaControl.yaml` | `/control/swagger/common/MediaControl.yaml` |
| `TimelineControl.yaml` | `/control/swagger/common/TimelineControl.yaml` |
| `SystemControl.yaml` | `/control/swagger/common/SystemControl.yaml` |
| `VideoControl.yaml` | `/control/swagger/Camera/VideoControl.yaml` |
| `PresetControl.yaml` | `/control/swagger/Camera/PresetControl.yaml` |
| `AudioControl.yaml` | `/control/swagger/Camera/AudioControl.yaml` |
| `LensControl.yaml` | `/control/swagger/Camera/LensControl.yaml` |
| `ColorCorrectionControl.yaml` | `/control/swagger/Camera/ColorCorrectionControl.yaml` |
| `Notification.yaml` | `/control/asyncAPI/Notification.yaml` — websocket events |
| `RESTAPIforBlackmagicCameras_2025-08.pdf` | Official Blackmagic PDF (Aug 2025 revision); `pdftotext` it if you need to grep |

## Camera model

This unit is a **Blackmagic Studio Camera 6K Pro** at `192.168.0.194`.
Sensor reports 6144×3456 and the default codec is `BRaw:8_1`.

## What this camera does NOT expose on current firmware

Notable by their absence in the camera's own `/control/documentation.html`
index:

- `LivestreamControl.yaml` — even though the Studio 6K Pro line supports
  livestreaming in hardware (and Blackmagic's PDF documents the
  `/livestreams/0/*` endpoints), this unit's firmware returns 404 on every
  one of those paths. A Camera OS update is likely required. Revisit after
  updating.
- Any preview / MJPEG / HLS / RTSP endpoint — no network video preview or
  still-frame endpoint on current firmware.
- `/slates/*` — full digital slate metadata is not served. The only clip
  naming hook available is `clipName` on `PUT /transports/0/record`.

The Livestream API in the official PDF is documented for the Studio and URSA
Broadcast G2 lines; expect it to light up once firmware catches up.

from __future__ import annotations

from typing import TypedDict


class SystemInfo(TypedDict, total=False):
    codecFormat: dict
    videoFormat: dict
    label: str
    model: str
    name: str


class VideoFormat(TypedDict, total=False):
    codec: str
    frameRate: str
    recordResolution: dict
    sensorResolution: dict


class RecordState(TypedDict, total=False):
    recording: bool
    recordingDisk: str


class Timecode(TypedDict, total=False):
    timecode: int
    displayMode: str


class Disk(TypedDict, total=False):
    deviceName: str
    volumeName: str
    uuid: str
    totalSpace: int
    remainingRecordTime: int


class Clip(TypedDict, total=False):
    clipUniqueId: int
    filePath: str
    fileName: str
    duration: int

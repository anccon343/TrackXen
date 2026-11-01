"""App settings, saved as JSON. Device addresses are set in the app (or found by discovery)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from trackxen.control.controller import DEFAULT_PAN_SPEEDS, DEFAULT_TILT_SPEEDS, Framing, Motion
from trackxen.core.tracking_settings import SLOTS, AppSettings, PresetLink, default_slots
from trackxen.rules.audio import AudioFollowConfig


def data_dir() -> Path:
    """Where settings and logs live: %APPDATA%/TrackXen when installed, ./data in development
    (TRACKXEN_DATA overrides both: a portable copy, or a test run)."""
    if os.environ.get("TRACKXEN_DATA"):
        return Path(os.environ["TRACKXEN_DATA"])
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("APPDATA", Path.home())) / "TrackXen"
    return Path(__file__).resolve().parents[3] / "data"


def models_dir() -> Path:
    """The models shipped with the app (read-only when installed)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "models"
    return Path(__file__).resolve().parents[3] / "models"


def engine_cache_dir() -> Path:
    """TensorRT engines built for this PC's GPU: must be writable (Program Files isn't)."""
    if getattr(sys, "frozen", False):
        return data_dir() / "trt-cache"
    return models_dir() / "trt-cache"


class LensConfig(BaseModel):
    hfov_wide: float = 60.0
    hfov_tele: float = 3.2
    zoom_max: float = 16384.0


class FaceRef(BaseModel):
    """A registered face as one camera uses it (Canon step 9): on or off; the list order is the priority."""

    id: str
    enabled: bool = True


class CameraConfig(BaseModel):
    id: str = "cam1"
    name: str = "Camera 1"
    model: Literal["birddog_p400", "ptzoptics_g2", "other"] = "birddog_p400"  # core/camera_models.py
    video: Literal["ndi", "capture", "stream"] = "ndi"  # where the picture comes from (control is always over IP)
    ndi: str = ""  # NDI source name, or a unique part of it
    capture_device: int = Field(0, ge=0, le=15)  # HDMI/SDI capture device number (video "capture")
    stream_url: str = ""  # video "stream": the camera's RTSP address ("" = its default, e.g. rtsp://IP:554/1)
    stream_transport: Literal["tcp", "udp"] = "tcp"  # how the RTSP stream's packets travel
    host: str = ""  # camera IP for VISCA
    visca_transport: Literal["sony", "udp", "tcp"] = "sony"  # Sony VISCA over IP (header), plain VISCA over UDP or TCP
    visca_port: int = 52381
    freed_port: int | None = None  # UDP port this PC receives the camera's FreeD on
    units_per_degree: float = 16.0  # VISCA position units per degree, pan (calibrated)
    tilt_units_per_degree: float | None = None  # ... tilt, when it differs from pan
    pan_sign: int = 1
    tilt_sign: int = 1
    video_delay: float = 0.08  # seconds from capture to arrival here (calibrated)
    low_bandwidth: bool = False
    lens: LensConfig = Field(default_factory=LensConfig)
    pan_speeds: list[float] = Field(default_factory=lambda: list(DEFAULT_PAN_SPEEDS))
    tilt_speeds: list[float] = Field(default_factory=lambda: list(DEFAULT_TILT_SPEEDS))
    motion: Motion = Field(default_factory=Motion)
    lost_after: float = 1.5  # seconds without the target before it counts as lost
    refind_window: float = 10.0  # seconds to keep looking for the lost target where it was last seen
    slots: list[AppSettings] = Field(default_factory=default_slots)  # Canon's 5 App Settings
    active_slot: int = Field(0, ge=0, lt=SLOTS)
    preset_links: dict[int, PresetLink] = Field(default_factory=dict)  # preset number (1..) -> behaviour
    faces: list[FaceRef] = Field(default_factory=list, max_length=7)  # registered faces, highest priority first
    vmix_input: str = ""  # the vMix input showing this camera (input key; its number also works)

    @model_validator(mode="before")
    @classmethod
    def _old_header_setting(cls, data):
        """Settings from before camera types had `use_header`: false meant plain VISCA over UDP."""
        if isinstance(data, dict) and data.get("use_header") is False and "visca_transport" not in data:
            data = {**data, "visca_transport": "udp"}
        return data

    @property
    def video_url(self) -> str:
        """The RTSP address actually used for video "stream"."""
        from trackxen.core.camera_models import stream_url

        return self.stream_url or stream_url(self.model, self.host)

    @property
    def slot(self) -> AppSettings:
        return self.slots[self.active_slot]

    @property
    def framing(self) -> Framing:
        """The active App Settings' composition (what the controller frames with)."""
        return self.slot.composition


class DetectionSettings(BaseModel):
    """Advanced mode: how certain the system must be before and while it tracks someone."""

    start_confidence: float = Field(0.35, ge=0.05, le=0.95)  # a new person needs this...
    confirm_frames: int = Field(2, ge=1, le=30)  # ...for this many frames in a row
    match_confidence: float = Field(0.3, ge=0.05, le=0.95)  # sure detections, matched first
    keep_confidence: float = Field(0.1, ge=0.01, le=0.9)  # weak detections only keep a known person
    keep_missing_seconds: float = Field(1.0, ge=0.1, le=10.0)  # how long a person survives unseen
    match_overlap: float = Field(0.2, ge=0.05, le=0.9)  # box overlap (IoU) needed to match

    def tracker_args(self) -> dict:
        """Keyword arguments for vision.tracker.PersonTracker."""
        return {"new_track_score": self.start_confidence, "confirm_hits": self.confirm_frames,
                "high_score": self.match_confidence, "low_score": self.keep_confidence,
                "max_missing": self.keep_missing_seconds, "match_iou": self.match_overlap}


class VmixConfig(BaseModel):
    host: str = ""  # the vMix PC (empty: no vMix; cameras are on air only by the manual switch)
    port: int = 8099


class MixerConfig(BaseModel):
    host: str = ""  # the MR18 / X Air mixer (empty: no audio-follow)
    port: int = 10024


class AppConfig(BaseModel):
    cameras: list[CameraConfig] = Field(default_factory=list)
    vmix: VmixConfig = Field(default_factory=VmixConfig)
    mixer: MixerConfig = Field(default_factory=MixerConfig)
    audio: AudioFollowConfig = Field(default_factory=AudioFollowConfig)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    performance_mode: str = "auto"  # "light", "auto" or "hyper"
    auto_preset: str = "normal"  # what Auto runs; the hardware test recommends it
    model_override: str = ""  # e.g. "yolo26m-pose-640": use this model instead of the preset's
    engines: list[str] = Field(default_factory=lambda: ["tensorrt", "cuda", "directml", "cpu"])
    web_host: str = "0.0.0.0"
    web_port: int = 8090
    preview_fps: float = 12.0
    log_keep_days: int = 30

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        if path.exists():
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=2), encoding="utf-8")
        tmp.replace(path)


def sim_config(cameras: int = 1, ptzoptics: int = 0) -> AppConfig:
    """Settings for the virtual cameras started by tools/sim_camera.py (camera k: NDI "TRACKXEN SIM k",
    VISCA port 52380 + k, FreeD to port 40000 + k). The last `ptzoptics` cameras are set up as PTZOptics
    G2 (start those with sim_camera.py --ptzoptics): plain VISCA, no FreeD, that type's units and
    speeds; their video stays NDI."""
    from trackxen.core.camera_models import camera_defaults

    out = []
    for k in range(1, cameras + 1):
        if k > cameras - ptzoptics:
            fields = {**camera_defaults("ptzoptics_g2", k), "video": "ndi", "video_delay": 0.08}
            out.append(CameraConfig(id=f"cam{k}", name=f"Sim PTZOptics {k}", ndi=f"TRACKXEN SIM {k}",
                                    host="127.0.0.1", **{**fields, "visca_port": 52380 + k}))
        else:
            out.append(CameraConfig(id=f"cam{k}", name=f"Sim camera {k}", ndi=f"TRACKXEN SIM {k}", host="127.0.0.1",
                                    visca_port=52380 + k, freed_port=40000 + k, video_delay=0.08))
    return AppConfig(cameras=out)

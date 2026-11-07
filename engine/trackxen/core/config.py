"""App settings, saved as JSON. Device addresses are set in the app (or found by discovery)."""

from __future__ import annotations

import itertools
import json
import os
import shutil
import sys
import time
from pathlib import Path

from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, ValidationError, ValidationInfo, field_validator, model_validator

from trackxen.control.controller import DEFAULT_PAN_SPEEDS, DEFAULT_TILT_SPEEDS, Framing, Motion
from trackxen.core.tracking_settings import SLOTS, AppSettings, FiniteModel, PresetLink, bad_value, default_slots
from trackxen.rules.audio import AudioFollowConfig

Port = Annotated[int, Field(ge=1, le=65535)]
PORT_FIELDS = ("port", "visca_port", "freed_port", "web_port")  # (earlier versions saved 0 for an emptied one)
Speed = Annotated[float, Field(gt=0.0)]  # degrees per second of one VISCA speed step


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


class LensConfig(FiniteModel):
    hfov_wide: float = Field(60.0, gt=0.0, lt=180.0)
    hfov_tele: float = Field(3.2, gt=0.0)
    zoom_max: float = Field(16384.0, gt=0.0)
    # Measured horizontal field of view by zoom position (tools/lens_probe.py): [[zoom, degrees], ...] from zoom 0
    # up, narrower each time. Its ends set the three values above. Empty: an exponential curve between the ends.
    zoom_table: list[tuple[float, float]] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _zooms_in(self) -> LensConfig:
        table = self.zoom_table
        if table:
            if len(table) < 2 or table[0][0] != 0:
                raise bad_value("the zoom table needs at least two points, the first at zoom 0")
            if any(z1 <= z0 for (z0, _), (z1, _) in zip(table, table[1:])):
                raise bad_value("the zoom table's zoom positions must rise")
            if not all(0.0 < h < 180.0 for _, h in table) or any(h1 >= h0 for (_, h0), (_, h1) in zip(table, table[1:])):
                raise bad_value("the zoom table's field of view must get narrower, between 0 and 180 degrees")
            self.hfov_wide, self.hfov_tele, self.zoom_max = table[0][1], table[-1][1], table[-1][0]
        if self.hfov_tele >= self.hfov_wide:
            raise bad_value("the lens must be narrower at full zoom: hfov_tele below hfov_wide")
        return self


class FaceRef(FiniteModel):
    """A registered face as one camera uses it (Registered Face Tracking): on or off; the list order is the priority."""

    id: str
    enabled: bool = True


class CameraConfig(FiniteModel):
    id: str = "cam1"
    name: str = "Camera 1"
    model: Literal["birddog_p400", "ptzoptics_g2", "ptzoptics_g2_12x", "other"] = "birddog_p400"  # core/camera_models.py
    video: Literal["ndi", "capture", "stream"] = "ndi"  # where the picture comes from (control is always over IP)
    ndi: str = ""  # NDI source name, or a unique part of it
    capture_device: int = Field(0, ge=0, le=15)  # HDMI/SDI capture device number (video "capture")
    stream_url: str = ""  # video "stream": the camera's RTSP address ("" = its default, e.g. rtsp://IP:554/1)
    stream_transport: Literal["tcp", "udp"] = "tcp"  # how the RTSP stream's packets travel
    host: str = ""  # camera IP for VISCA
    visca_transport: Literal["sony", "udp", "tcp"] = "sony"  # Sony VISCA over IP (header), plain VISCA over UDP or TCP
    visca_port: Port = 52381
    freed_port: Port | None = None  # UDP port this PC receives the camera's FreeD on
    units_per_degree: float = Field(16.0, gt=0.0)  # VISCA position units per degree, pan (calibrated)
    tilt_units_per_degree: float | None = Field(None, gt=0.0)  # ... tilt, when it differs from pan
    pan_sign: int = 1
    tilt_sign: int = 1
    video_delay: float = 0.08  # seconds from capture to arrival here (calibrated)
    low_bandwidth: bool = False
    lens: LensConfig = Field(default_factory=LensConfig)
    pan_speeds: list[Speed] = Field(default_factory=lambda: list(DEFAULT_PAN_SPEEDS), min_length=1)
    tilt_speeds: list[Speed] = Field(default_factory=lambda: list(DEFAULT_TILT_SPEEDS), min_length=1)
    motion: Motion = Field(default_factory=Motion)
    lost_after: float = 1.5  # seconds without the target before it counts as lost
    refind_window: float = 10.0  # seconds to keep looking for the lost target where it was last seen
    slots: list[AppSettings] = Field(default_factory=default_slots)  # the 5 App Settings
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

    @field_validator("slots")
    @classmethod
    def _five(cls, v: list[AppSettings]) -> list[AppSettings]:
        """Always 5 App Settings (a show file with fewer would stop the camera from starting)."""
        return (v + default_slots()[len(v):])[:SLOTS]

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


class DetectionSettings(FiniteModel):
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


class VmixConfig(FiniteModel):
    host: str = ""  # the vMix PC (empty: no vMix; cameras are on air only by the manual switch)
    port: Port = 8099


class MixerConfig(FiniteModel):
    host: str = ""  # the MR18 / X Air mixer (empty: no audio-follow)
    port: Port = 10024


class AppConfig(FiniteModel):
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
    web_port: Port = 8090
    preview_fps: float = 12.0
    log_keep_days: int = 30
    _load_notes: list[str] = PrivateAttr(default_factory=list)

    @property
    def load_notes(self) -> list[str]:
        """What load() had to repair or replace (the app puts it in its log)."""
        return self._load_notes

    @model_validator(mode="after")
    def _cameras_apart(self, info: ValidationInfo) -> AppConfig:
        """Each camera needs its own id (it names the camera everywhere) and its own FreeD port (only one
        connection can receive on a port). Settings saved by earlier versions are repaired when loading:
        the later camera gets a free id, or no FreeD (its position is then asked over VISCA)."""
        notes = (info.context or {}).get("notes")
        ids, ports = set(), set()
        for cam in self.cameras:
            if cam.id in ids:
                if notes is None:
                    raise bad_value("two cameras have the id {id}: each camera needs its own", id=cam.id)
                taken = {c.id for c in self.cameras}
                new = next(f"cam{n}" for n in itertools.count(1) if f"cam{n}" not in taken)
                notes.append(f"camera {cam.name!r} had the id {cam.id!r} of another camera: it is {new} now")
                cam.id = new
            ids.add(cam.id)
            if cam.freed_port is not None and cam.freed_port in ports:
                if notes is None:
                    raise bad_value("two cameras use FreeD port {port}: each camera needs its own", port=cam.freed_port)
                notes.append(f"camera {cam.id!r} used the FreeD port {cam.freed_port} of another camera: "
                             "FreeD is off for it now")
                cam.freed_port = None
            elif cam.freed_port is not None:
                ports.add(cam.freed_port)
        return self

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        """Read the settings. TrackXen must start whatever the file holds: values that are refused now (saved
        by an earlier version, or edited by hand) go back to their defaults, and a file that can't be read at
        all (cut short by a power cut, say) is kept as settings.json.broken-<time> while the last good copy
        (settings.json.bak) or else the defaults are used. What happened is printed and kept in load_notes."""
        if not path.exists():
            return cls()
        notes: list[str] = []
        try:
            cfg = cls._read(path, notes)
        except ValueError as e:  # not JSON, cut short, not text, or not settings at all
            broken = path.with_name(f"{path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
            path.replace(broken)
            reason = str(e).splitlines()[0] if str(e) else type(e).__name__
            notes = [f"{path.name} can't be read ({reason}); it is kept as {broken.name}"]
            backup, repaired = path.with_name(path.name + ".bak"), []
            try:
                cfg = cls._read(backup, repaired)
                notes += [f"using the last good copy, {backup.name}", *repaired]
            except (OSError, ValueError):
                cfg = cls()
                notes.append("starting with the default settings")
            try:
                cfg.save(path)  # the next start reads it again
            except OSError as e:
                notes.append(f"can't write {path.name}: {e}")
        for note in notes:
            if sys.stderr:
                print(f"TrackXen settings: {note}", file=sys.stderr)
        cfg._load_notes = notes
        return cfg

    @classmethod
    def _read(cls, path: Path, notes: list[str]) -> AppConfig:
        """Settings as saved (json.dumps); a value refused now goes back to its default, noted."""
        return cls._repaired(json.loads(path.read_text(encoding="utf-8")), notes, lambda error: True)

    @classmethod
    def from_show(cls, data: dict, notes: list[str]) -> AppConfig:
        """A show file, checked as strictly as settings sent from the settings page, except for what earlier
        versions saved and is refused now: a camera id or FreeD port used twice, and a port out of range (0
        from an emptied field). Those are repaired as in settings.json, noted. Anything else raises."""
        return cls._repaired(data, notes, _port_out_of_range)

    @classmethod
    def _repaired(cls, data, notes: list[str], may_forget) -> AppConfig:
        """Validate `data`, setting a value refused now that `may_forget` takes back to its default, noted."""
        for _ in range(500):  # one refused value at a time
            try:
                return cls.model_validate(data, context={"notes": notes})
            except ValidationError as e:
                for error in e.errors(include_url=False):
                    if may_forget(error) and _forget(data, error["loc"]):
                        notes.append(f"{'.'.join(map(str, error['loc']))}: {error['msg']}; using the default")
                        break
                else:
                    raise
        raise ValueError("too many values that are not valid")

    def save(self, path: Path) -> None:
        """Write the settings so that a crash or power cut can't leave a damaged file: the new file is on
        the disk before it replaces the old one, which is kept as settings.json.bak (the last good copy)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.model_dump(), indent=2))
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        tmp.replace(path)


def _port_out_of_range(error: dict) -> bool:
    loc = error["loc"]
    return bool(loc) and loc[-1] in PORT_FIELDS and error["type"] in ("greater_than_equal", "less_than_equal")


def _forget(data, loc: tuple) -> bool:
    """Remove the saved value at `loc` (the innermost named setting that holds it), so its default is used."""
    holder, key, node = None, None, data
    for part in loc:
        if isinstance(node, dict) and part in node:
            holder, key, node = node, part, node[part]
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            node = node[part]
        else:
            break
    if holder is None:
        return False
    del holder[key]
    return True


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

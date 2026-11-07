"""Performance modes (Light / Auto / HYPER) and the hardware-test recommendation.

A preset fixes how heavy the vision work is: model size, input resolution, how many
detections per camera per second, NDI bandwidth and preview rate. The hardware test
measures how many images per second this PC really processes for each preset and
recommends a mode with enough headroom (more when vMix runs on the same PC).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Preset:
    name: str
    model: str  # model family, e.g. "yolo26s-pose"
    imgsz: int  # model input size in pixels
    detect_fps: float  # detections per camera per second; 0 = every frame
    low_bandwidth: bool  # NDI low-bandwidth stream for detection
    preview_fps: float
    description: str = ""

    def model_file(self, batch: int) -> str:
        return f"{self.model}-{self.imgsz}-b{batch}.onnx"


PRESETS: dict[str, Preset] = {p.name: p for p in [
    Preset("light", "yolo26n-pose", 640, 15, True, 8, "smallest model, 15 detections/s per camera"),
    Preset("normal", "yolo26s-pose", 640, 30, False, 12, "small model, 30 detections/s per camera"),
    Preset("high", "yolo26m-pose", 640, 30, False, 12, "medium model, 30 detections/s per camera"),
    Preset("hyper", "yolo26m-pose", 960, 0, False, 15, "medium model at high resolution, every frame"),
]}
AUTO_CHOICES = ("high", "normal", "light")  # best first


@dataclass
class PresetCheck:
    preset: str
    needed_per_s: float  # images per second the preset needs for all cameras
    measured_per_s: float  # images per second this PC managed (0 = not measured)
    margin: float  # measured / needed

    @property
    def fits(self) -> bool:
        return self.measured_per_s > 0 and self.margin >= 1.0


@dataclass
class Recommendation:
    mode: str  # "hyper", "auto", "light" or "insufficient"
    auto_preset: str | None  # what Auto would run
    headroom: float
    cameras: int
    source_fps: float
    checks: list[PresetCheck] = field(default_factory=list)

    def text(self) -> str:
        lines = [f"Recommended mode: {self.mode.upper()}"
                 + (f" (Auto runs '{self.auto_preset}': {PRESETS[self.auto_preset].description})"
                    if self.mode == "auto" and self.auto_preset else ""),
                 f"For {self.cameras} camera(s) at {self.source_fps:g} fps, with {self.headroom:g}x headroom:"]
        for c in self.checks:
            p = PRESETS[c.preset]
            state = "OK " if c.fits else "too slow" if c.measured_per_s else "not measured"
            lines.append(f"  {c.preset:7s} {p.model} @ {p.imgsz}: needs {c.needed_per_s:6.0f} images/s, "
                         f"measured {c.measured_per_s:6.0f} -> {c.margin:4.1f}x  {state}")
        if self.mode == "insufficient":
            lines.append("This PC is too slow even for Light mode with this many cameras: use fewer cameras "
                         "or a PC with an NVIDIA graphics card.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [{**asdict(c), "fits": c.fits} for c in self.checks]
        return d


def needed_images_per_s(preset: Preset, cameras: int, source_fps: float) -> float:
    per_camera = source_fps if preset.detect_fps == 0 else min(preset.detect_fps, source_fps)
    return per_camera * cameras


def recommend(measured: dict[tuple[str, int], float], cameras: int, source_fps: float = 30.0,
              shared_with_vmix: bool = False) -> Recommendation:
    """`measured[(model, imgsz)]` = images per second this PC processed (batched like the app)."""
    headroom = 2.0 if shared_with_vmix else 1.4
    checks = []
    for name, p in PRESETS.items():
        need = needed_images_per_s(p, cameras, source_fps) * headroom
        got = measured.get((p.model, p.imgsz), 0.0)
        checks.append(PresetCheck(name, round(need, 1), round(got, 1), round(got / need, 2) if need else 0.0))
    fits = {c.preset: c.fits for c in checks}
    auto = next((name for name in AUTO_CHOICES if fits[name]), None)
    if fits["hyper"]:
        mode = "hyper"
    elif auto in ("high", "normal"):
        mode = "auto"
    elif auto == "light":
        mode = "light"
    else:
        mode = "insufficient"
    return Recommendation(mode, auto, headroom, cameras, source_fps, checks)

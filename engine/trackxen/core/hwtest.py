"""Hardware test: measures this PC with the real models and engines, then recommends a mode.

Everything reported is measured here: detection throughput per preset (resizing and decoding
included, batched and pipelined like the app), and the per-frame CPU work around it (preview
JPEG, NDI frame conversion). Run it from the UI or `python tools/hardware_test.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import psutil

from trackxen.core.performance import PRESETS, recommend
from trackxen.core.stats import percentile
from trackxen.vision.runtime import available_engines

try:
    import pynvml
except ImportError:
    pynvml = None


def system_info() -> dict:
    info = {
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "memory_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "engines": available_engines(),
        "gpus": [],
    }
    if pynvml:
        try:
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                info["gpus"].append({"name": pynvml.nvmlDeviceGetName(h),
                                     "memory_gb": round(pynvml.nvmlDeviceGetMemoryInfo(h).total / 2**30, 1),
                                     "driver": pynvml.nvmlSystemGetDriverVersion()})
        except Exception:  # noqa: BLE001 - no NVIDIA GPU or driver
            pass
    return info


PEOPLE_PHOTO = Path(__file__).with_name("assets") / "people.jpg"  # Ultralytics' bus.jpg (AGPL), shipped with the app


def test_frame(width: int = 1920, height: int = 1080) -> np.ndarray:
    """A 1080p frame with real people in it (a sample photo), like a camera picture."""
    photo = cv2.imread(str(PEOPLE_PHOTO))
    frame = np.full((height, width, 3), 30, dtype=np.uint8)
    h = height
    w = round(photo.shape[1] * h / photo.shape[0])
    frame[:, (width - w) // 2:(width - w) // 2 + w] = cv2.resize(photo, (w, h))
    return frame


def measure_preset_model(model_path: Path, engines: tuple[str, ...], batch: int, frame: np.ndarray, seconds: float,
                         cache_dir: Path, cameras: int = 4) -> dict:
    """One batch at a time first (how long one batch takes, and its stages), then the throughput
    with the GPU and CPU stages overlapping like the vision worker runs them.

    The model opens on the first of `engines` that works, as in the vision worker. A batch holds
    one frame per camera at most (the worker doesn't wait for a camera's next frame), so with 2 or
    3 cameras the batch-of-4 model runs with empty rows, as it does in the app."""
    from trackxen.vision.pipeline import throughput
    from trackxen.vision.yolo_pose import PoseDetector

    loaded = time.perf_counter()
    detector = PoseDetector(model_path, cache_dir=cache_dir, engines=engines, conf=0.1)
    load_s = time.perf_counter() - loaded
    frames = [frame] * max(1, min(batch, cameras))
    for _ in range(3):
        detector.detect(frames)
    times, stages = [], []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end or len(times) < 10:
        t0 = time.perf_counter()
        detector.detect(frames)
        times.append(time.perf_counter() - t0)
        stages.append(detector.last_timing)
    p50 = statistics.median(times)
    return {"engine": detector.engine, "batch": batch, "runs": len(times), "load_s": round(load_s, 2),
            "batch_ms_p50": round(p50 * 1000, 2),
            "batch_ms_p95": round(percentile(sorted(times), 95) * 1000, 2),
            "images_per_s": round(throughput(detector, frames, seconds), 1),
            "images_per_s_one_at_a_time": round(len(frames) / p50, 1),
            # where the time goes: image preparation (CPU), the model (GPU), decoding (CPU)
            **{f"{k}_p50": round(statistics.median(s[k] for s in stages), 2) for k in ("pre_ms", "infer_ms", "post_ms")}}


def measure_cpu_work(frame: np.ndarray, runs: int = 50) -> dict:
    """Per-frame CPU work besides detection: converting a 1080p NDI frame and the preview JPEG."""
    bgrx = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    t0 = time.perf_counter()
    for _ in range(runs):
        cv2.cvtColor(bgrx, cv2.COLOR_BGRA2BGR)  # what devices/ndi_in.to_bgr does with every frame
    copy_ms = (time.perf_counter() - t0) / runs * 1000
    t0 = time.perf_counter()
    for _ in range(runs):
        small = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
        cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    jpeg_ms = (time.perf_counter() - t0) / runs * 1000
    return {"frame_copy_ms": round(copy_ms, 2), "preview_jpeg_ms": round(jpeg_ms, 2)}


def run(models_dir: Path, cameras: int = 4, source_fps: float = 30.0, shared_with_vmix: bool = False,
        tensorrt: bool = True, seconds: float = 4.0, progress: Callable[[str], None] = print,
        cache_dir: Path | None = None) -> dict:
    """Measure every preset's model on the engine the app will use, and recommend a mode.

    TensorRT (NVIDIA) is measured by default because the app runs on it; building its engines
    takes a few minutes per model the first time, and the app reuses them afterwards.
    `tensorrt=False` measures the next engine instead (quicker, but conservative numbers).
    ONNX Runtime lists the engines it was built with, not the ones this PC can run: an engine
    that doesn't load falls back to the next one, as in the app (without NVIDIA: the processor).
    """
    info = system_info()
    progress(f"PC: {info['cpu']} ({info['cpu_threads']} threads), {info['memory_gb']} GB RAM, "
             f"GPU: {', '.join(g['name'] for g in info['gpus']) or 'none (NVIDIA)'}; engines {info['engines']}")
    engines = [e for e in ("tensorrt", "cuda", "directml", "cpu") if e in info["engines"]]
    if not tensorrt and len(engines) > 1:
        engines = [e for e in engines if e != "tensorrt"]
        progress("quick test: TensorRT skipped, so the numbers are lower than the app will really reach")
    elif engines[0] == "tensorrt":
        progress("TensorRT: the first run builds an engine per model (a few minutes each); later runs are fast")
    batch = 1 if cameras <= 1 else 4
    frame = test_frame()
    results, measured = {}, {}
    for key in sorted({(p.model, p.imgsz) for p in PRESETS.values()}):
        model, imgsz = key
        path = models_dir / f"{model}-{imgsz}-b{batch}.onnx"
        name = f"{model} @ {imgsz}"
        if not path.exists():
            progress(f"{name}: model file missing ({path.name}), skipped")
            continue
        progress(f"measuring {name} on {engines[0]} ...")
        try:
            r = measure_preset_model(path, tuple(engines), batch, frame, seconds, cache_dir or models_dir / "trt-cache",
                                     cameras)
        except Exception as e:  # noqa: BLE001 - report and continue with the other models
            progress(f"{name}: failed: {str(e).splitlines()[0][:200]}")
            continue
        if r["engine"] != engines[0]:
            progress(f"  {engines[0]} didn't load: measured on {r['engine']}")
            # not tried again for the next models (a failing TensorRT build can take minutes)
            engines = engines[engines.index(r["engine"]):]
        results[name] = r
        measured[key] = r["images_per_s"]
        progress(f"  {r['images_per_s']} images/s (batch of {batch}: {r['batch_ms_p50']} ms median, "
                 f"{r['batch_ms_p95']} ms 95th)")
    cpu = measure_cpu_work(frame)
    rec = recommend(measured, cameras, source_fps, shared_with_vmix)
    progress(rec.text())
    engine = next((r["engine"] for r in results.values()), engines[0])  # the one really measured
    return {"time": dt.datetime.now().isoformat(timespec="seconds"), "system": info, "engine": engine,
            "cameras": cameras, "source_fps": source_fps, "shared_with_vmix": shared_with_vmix,
            "models": results, "cpu_work": cpu, "recommendation": rec.to_dict(), "summary": rec.text()}


def save(report: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (folder / f"hardware-test-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = folder / f"hardware-test-{stamp}.txt"
    s = report["system"]
    text.write_text("\n".join([
        f"TrackXen hardware test, {report['time']}",
        f"PC: {s['cpu']}, {s['cpu_threads']} threads, {s['memory_gb']} GB RAM, {s['os']}",
        f"GPU: {', '.join(g['name'] + ' ' + str(g['memory_gb']) + ' GB, driver ' + g['driver'] for g in s['gpus']) or 'none'}",
        f"Engine measured: {report['engine']} (available: {', '.join(s['engines'])})",
        f"Per-frame CPU work: frame copy {report['cpu_work']['frame_copy_ms']} ms, "
        f"preview JPEG {report['cpu_work']['preview_jpeg_ms']} ms",
        "", report["summary"], ""]), encoding="utf-8")
    return text

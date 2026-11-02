"""Check that this PC can run the tracker: GPU, YOLO26 (PyTorch and ONNX Runtime), TensorRT, NDI.

  python tools/env_check.py           # full check; downloads yolo26n-pose once (a few MB)
  python tools/env_check.py --quick   # skip the model export and speed tests

The ONNX Runtime part runs in a separate process without PyTorch, like the installed app:
PyTorch and ONNX Runtime ship different cuDNN builds that clash in one process.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("YOLO_AUTOINSTALL", "False")  # never let Ultralytics pip-install things

from _common import results_path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
REPORT: dict = {}


def show(status: str, name: str, detail: str = "") -> None:
    print(f"[{status:4s}] {name}{': ' + detail if detail else ''}", flush=True)
    REPORT[name] = {"status": status, "detail": detail}


def time_runs(fn, warmup: int, runs: int) -> float:
    for _ in range(warmup):
        fn()
    start = time.perf_counter()
    for _ in range(runs):
        fn()
    return (time.perf_counter() - start) / runs * 1000


def test_image():
    import cv2

    # the people photo shipped with the app (not imported from trackxen: this process has PyTorch loaded)
    return cv2.imread(str(ROOT / "engine" / "trackxen" / "core" / "assets" / "people.jpg"))


# --- parent process: PyTorch side ---------------------------------------------------


def check_pytorch() -> None:
    show("OK", "python", f"{platform.python_version()} on {platform.platform()}")
    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            show("OK", "pytorch (development only)", f"{torch.__version__}, CUDA {torch.version.cuda}, "
                                                     f"{props.name}, {props.total_memory / 2**30:.1f} GB")
        else:
            show("WARN", "pytorch (development only)", f"{torch.__version__}, no CUDA GPU visible")
    except Exception as e:  # noqa: BLE001 - report every failure, keep checking
        show("FAIL", "pytorch (development only)", str(e))


def export_models() -> None:
    from ultralytics import YOLO

    MODELS.mkdir(exist_ok=True)
    image = test_image()
    model = YOLO(str(MODELS / "yolo26n-pose.pt"))
    result = model.predict(image, device=0, verbose=False)[0]
    people = int((result.boxes.conf > 0.5).sum()) if result.boxes is not None else 0
    ms = time_runs(lambda: model.predict(image, device=0, verbose=False), 5, 30)
    show("OK", "yolo26n-pose (PyTorch GPU)", f"{people} people in test image, {ms:.1f} ms per image")
    names = []
    for batch in (1, 4):
        # nms=False exports YOLO26's NMS-free (end-to-end) head: (batch, 300, 57)
        exported = Path(model.export(format="onnx", imgsz=640, batch=batch, simplify=True, nms=False,
                                     verbose=False))
        target = MODELS / f"yolo26n-pose-b{batch}.onnx"
        exported.replace(target)
        names.append(target.name)
    show("OK", "onnx export", ", ".join(names))


# --- child process: what the installed app uses ---------------------------------------


def check_onnxruntime() -> None:
    import onnxruntime as ort

    from trackxen.vision.runtime import available_engines, prepare_gpu_libraries
    from trackxen.vision.yolo_pose import PoseDetector

    prepare_gpu_libraries()
    show("OK", "onnxruntime", f"{ort.__version__}, engines: {', '.join(available_engines())}")
    try:
        import tensorrt_libs  # type: ignore

        dlls = sorted(f for f in os.listdir(os.path.dirname(tensorrt_libs.__file__)) if f.startswith("nvinfer_"))
        show("OK", "tensorrt libraries", dlls[0] if dlls else "none")
    except ImportError:
        show("WARN", "tensorrt libraries", "not installed")

    image = test_image()
    for engine in ("tensorrt", "cuda", "cpu"):
        for batch in (1, 4):
            name = f"onnxruntime {engine} batch {batch}"
            model = MODELS / f"yolo26n-pose-b{batch}.onnx"
            if not model.exists():
                show("SKIP", name, "model not exported (run without --quick)")
                continue
            try:
                built = time.perf_counter()
                detector = PoseDetector(model, cache_dir=MODELS / "trt-cache", engines=(engine,), conf=0.5)
                frames = [image] * batch
                people = detector.detect(frames)[0]
                setup = time.perf_counter() - built
                ms = time_runs(lambda d=detector, f=frames: d.detect(f), 3, 5 if engine == "cpu" else 30)
                show("OK", name, f"{ms:.1f} ms per batch ({ms / batch:.1f} ms per image, including "
                                 f"resize and decoding), {len(people)} people, first load {setup:.1f} s")
            except Exception as e:  # noqa: BLE001
                cause = e.__cause__ or e
                show("FAIL", name, str(cause).splitlines()[0][:400])


def check_ndi() -> None:
    try:
        from trackxen.devices.ndi_in import find_sources

        sources = find_sources(3.0)
        show("OK", "ndi", f"{len(sources)} source(s): " + (", ".join(s.name for s in sources) or "none found"))
    except Exception as e:  # noqa: BLE001
        show("FAIL", "ndi", str(e))


def main(args) -> int:
    if args.runtime_only:
        check_onnxruntime()
        check_ndi()
        Path(args.report).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
        return 0

    check_pytorch()
    if not args.quick:
        try:
            export_models()
        except Exception as e:  # noqa: BLE001
            show("FAIL", "model export", str(e).splitlines()[0][:300])
    child_report = results_path("env-check-runtime", ".json")
    child = subprocess.run([sys.executable, __file__, "--runtime-only", "--report", str(child_report)], check=False)
    if child_report.exists():
        REPORT.update(json.loads(child_report.read_text(encoding="utf-8")))
        child_report.unlink()
    if child.returncode != 0:  # e.g. ONNX Runtime missing, a DLL that doesn't load, a crash in a GPU engine
        show("FAIL", "onnxruntime check", f"stopped early (exit code {child.returncode}); its error is printed above")
    path = results_path("env-check", ".json")
    path.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    print(f"saved {path}")
    return 1 if any(v["status"] == "FAIL" for v in REPORT.values()) else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--runtime-only", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--report", help=argparse.SUPPRESS)
    sys.exit(main(p.parse_args()))

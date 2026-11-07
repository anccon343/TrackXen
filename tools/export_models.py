"""Development tool: export YOLO26-pose models to ONNX for the app (sizes x input resolutions x batch).

  python tools/export_models.py                    # n, s, m at 640 and 960, batch 1 and 4
  python tools/export_models.py --sizes n s --imgsz 640

Files: models/yolo26{size}-pose-{imgsz}-b{batch}.onnx (the 640 versions also keep the short
name yolo26{size}-pose-b{batch}.onnx). Exported with nms=False: YOLO26's NMS-free output.
Needs PyTorch + Ultralytics (development environment only); downloads weights once.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("YOLO_AUTOINSTALL", "False")

MODELS = Path(__file__).resolve().parent.parent / "models"


def main(args) -> int:
    from ultralytics import YOLO

    MODELS.mkdir(exist_ok=True)
    for size in args.sizes:
        weights = MODELS / f"yolo26{size}-pose.pt"
        model = YOLO(str(weights))
        for imgsz in args.imgsz:
            for batch in args.batch:
                target = MODELS / f"yolo26{size}-pose-{imgsz}-b{batch}.onnx"
                if target.exists() and not args.force:
                    print(f"exists: {target.name}")
                    continue
                exported = Path(model.export(format="onnx", imgsz=imgsz, batch=batch, simplify=True, nms=False,
                                             verbose=False))
                exported.replace(target)
                if imgsz == 640:
                    shutil.copyfile(target, MODELS / f"yolo26{size}-pose-b{batch}.onnx")
                print(f"exported: {target.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sizes", nargs="+", default=["n", "s", "m"])
    p.add_argument("--imgsz", nargs="+", type=int, default=[640, 960])
    p.add_argument("--batch", nargs="+", type=int, default=[1, 4])
    p.add_argument("--force", action="store_true")
    sys.exit(main(p.parse_args()))

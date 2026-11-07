"""Measure this PC and recommend TrackXen's performance mode (Light / Auto / HYPER).

  python tools/hardware_test.py                       # 4 cameras at 30 fps
  python tools/hardware_test.py --cameras 2 --fps 50 --shared-with-vmix
  python tools/hardware_test.py --quick               # skip TensorRT (faster, conservative numbers)

Writes a readable report (.txt) and the raw numbers (.json) to the app's data folder.
"""

from __future__ import annotations

import argparse
import sys

from trackxen.core import hwtest
from trackxen.core.config import data_dir, models_dir


def main(args) -> int:
    report = hwtest.run(models_dir(), cameras=args.cameras, source_fps=args.fps,
                        shared_with_vmix=args.shared_with_vmix, tensorrt=not args.quick, seconds=args.seconds)
    path = hwtest.save(report, data_dir() / "hardware-tests")
    print(f"\nreport saved: {path}")
    return 0 if report["recommendation"]["mode"] != "insufficient" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cameras", type=int, default=4)
    p.add_argument("--fps", type=float, default=30.0, help="camera frame rate")
    p.add_argument("--shared-with-vmix", action="store_true", help="vMix runs on this PC too (more headroom)")
    p.add_argument("--quick", action="store_true", help="skip TensorRT: quicker, but conservative numbers")
    p.add_argument("--seconds", type=float, default=4.0, help="measuring time per model")
    sys.exit(main(p.parse_args()))

"""List NDI sources, or receive one and report frame rate, size and timing.

  python tools/ndi_probe.py                       # list sources
  python tools/ndi_probe.py "BIRDDOG-12345 (CAM)" --seconds 10 --snapshot
  python tools/ndi_probe.py "BIRDDOG-12345 (CAM)" --low-bandwidth
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import cv2

from _common import results_path

from trackxen.devices.ndi_in import NdiReceiver, find_sources


def main(args) -> int:
    if not args.source:
        print(f"looking for NDI sources for {args.timeout:g} s ...")
        sources = find_sources(args.timeout)
        for s in sources:
            print(f"  {s.name}    (host {s.host_name})")
        if not sources:
            print("no NDI sources found")
        return 0

    rx = NdiReceiver(args.source, low_bandwidth=args.low_bandwidth)
    try:
        rx.start(timeout=args.timeout)
    except LookupError as e:
        print(f"{e} (run without a source name to list them)")
        return 1
    print(f"receiving '{args.source}' ({'low' if args.low_bandwidth else 'full'} bandwidth) "
          f"for {args.seconds:g} s ...")
    arrivals: list[float] = []
    ages: list[float] = []
    last_number = 0
    frame = None
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            frame = rx.latest()
            if frame and frame.number != last_number:
                last_number = frame.number
                arrivals.append(frame.received)
                if frame.timestamp:
                    ages.append(time.time() - frame.timestamp)
            time.sleep(0.002)
    finally:
        rx.stop()

    if len(arrivals) < 2 or frame is None:
        print("no video received")
        return 1
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    h, w = frame.image.shape[:2]
    print(f"resolution: {w}x{h}")
    print(f"frame rate: {(len(arrivals) - 1) / (arrivals[-1] - arrivals[0]):.2f} fps "
          f"(gap median {statistics.median(gaps) * 1000:.1f} ms, max {max(gaps) * 1000:.1f} ms)")
    if ages:
        print(f"sender timestamp age: median {statistics.median(ages) * 1000:.0f} ms "
              "(only meaningful if the camera's clock is in sync with this PC)")
    if args.snapshot:
        path = results_path("ndi-snapshot", ".png")
        cv2.imwrite(str(path), frame.image)
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="NDI source name (omit to list sources)")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=5.0, help="how long to look for sources")
    p.add_argument("--low-bandwidth", action="store_true", help="receive NDI's low-bandwidth stream")
    p.add_argument("--snapshot", action="store_true", help="save the last frame to lab-results/")
    try:
        sys.exit(main(p.parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)

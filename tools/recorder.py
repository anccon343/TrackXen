"""Record a camera's NDI video together with its FreeD positions, for replay tests without hardware.

  python tools/recorder.py "BIRDDOG-12345 (CAM)" --freed-port 40001 --seconds 120

Writes lab-results/rec-<time>/ with video.mp4, frames.csv (arrival time of each frame)
and freed.csv (camera position over time). Both CSVs use the same clock.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import cv2

from _common import RESULTS_DIR, results_path, write_csv

from trackxen.devices import freed
from trackxen.devices.ndi_in import NdiReceiver


async def main(args) -> int:
    folder = results_path("rec", "")
    folder.mkdir(parents=True)
    rx = NdiReceiver(args.source, low_bandwidth=args.low_bandwidth)
    try:
        rx.start()
    except LookupError as e:
        print(f"{e} (list sources with tools/ndi_probe.py)")
        folder.rmdir()
        return 1
    freed_rows: list[list] = []
    transport = None
    if args.freed_port:
        transport = await freed.listen(args.freed_port, lambda p, a, t: freed_rows.append(
            [round(t, 4), round(p.pan, 4), round(p.tilt, 4), p.zoom, p.focus]))

    writer = None
    frame_rows: list[list] = []
    last = 0
    start = time.monotonic()
    print(f"recording '{args.source}' to {folder.relative_to(RESULTS_DIR.parent)} for {args.seconds:g} s ...")
    try:
        while time.monotonic() - start < args.seconds:
            frame = rx.latest()
            if frame and frame.number != last:
                last = frame.number
                image = frame.image
                if args.max_width and image.shape[1] > args.max_width:
                    scale = args.max_width / image.shape[1]
                    image = cv2.resize(image, (args.max_width, round(image.shape[0] * scale)))
                if writer is None:
                    h, w = image.shape[:2]
                    writer = cv2.VideoWriter(str(folder / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                             args.fps, (w, h))
                writer.write(image)
                frame_rows.append([len(frame_rows), round(frame.received, 4), frame.timestamp])
            await asyncio.sleep(0.002)
    finally:
        rx.stop()
        if transport:
            transport.close()
        if writer:
            writer.release()
    write_csv(folder / "frames.csv", ["index", "t_monotonic", "sender_timestamp"], frame_rows)
    if freed_rows:
        write_csv(folder / "freed.csv", ["t_monotonic", "pan_deg", "tilt_deg", "zoom_raw", "focus_raw"], freed_rows)
    print(f"saved {len(frame_rows)} frames and {len(freed_rows)} FreeD positions to {folder}")
    return 0 if frame_rows else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="NDI source name (list them with ndi_probe.py)")
    p.add_argument("--freed-port", type=int)
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--fps", type=float, default=30.0, help="frame rate written into the video file")
    p.add_argument("--max-width", type=int, default=1920, help="downscale wider frames (0 = keep)")
    p.add_argument("--low-bandwidth", action="store_true")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

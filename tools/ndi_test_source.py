"""Send a moving test picture as an NDI source, to check NDI receiving without a camera.

  python tools/ndi_test_source.py --name "TRACKXEN TEST" --seconds 60

A grey "person" block walks left and right, so detection tests have something to see.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from fractions import Fraction

import cv2
import numpy as np
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC


def draw(frame_no: int, width: int, height: int) -> np.ndarray:
    img = np.zeros((height, width, 4), dtype=np.uint8)
    img[:, :, :3] = (40, 30, 25)
    x = int(width / 2 + width * 0.35 * math.sin(frame_no / 60))
    y = int(height * 0.55)
    cv2.circle(img, (x, y - 170), 45, (170, 170, 170, 255), -1)  # head
    cv2.rectangle(img, (x - 70, y - 120), (x + 70, y + 160), (120, 120, 120, 255), -1)  # body
    cv2.putText(img, f"TRACKXEN NDI TEST  frame {frame_no}", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (255, 255, 255, 255), 2)
    return img


def main(args) -> int:
    width, height = args.width, args.height
    sender = Sender(args.name)
    vf = VideoSendFrame()
    vf.set_resolution(width, height)
    vf.set_frame_rate(Fraction(args.fps, 1))
    vf.set_fourcc(FourCC.BGRX)
    sender.set_video_frame(vf)
    sender.open()
    print(f"sending NDI source '{args.name}' ({width}x{height} at {args.fps} fps) for {args.seconds:g} s")
    start = time.monotonic()
    frame_no = 0
    try:
        while time.monotonic() - start < args.seconds:
            sender.write_video(draw(frame_no, width, height).ravel())
            frame_no += 1
            next_time = start + frame_no / args.fps
            time.sleep(max(0.0, next_time - time.monotonic()))
    finally:
        sender.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default="TRACKXEN TEST")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--seconds", type=float, default=60.0)
    try:
        sys.exit(main(p.parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)

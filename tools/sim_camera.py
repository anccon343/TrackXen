"""Run a virtual P400: VISCA over IP, FreeD and live NDI video of a stage with a walking speaker.

  python tools/sim_camera.py
  python tools/sim_camera.py --camera 2      (camera 2: "TRACKXEN SIM 2", VISCA 52382, FreeD to 40002)
  python tools/sim_camera.py --camera 3 --ptzoptics   (behaves like a PTZOptics G2: plain VISCA, no FreeD)

Point the tracker at NDI source "<this PC> (TRACKXEN SIM 1)", VISCA 127.0.0.1:52381, FreeD port 40001
(`python -m trackxen --sim --sim-cameras 4 --sim-ptzoptics 2` sets cameras 3 and 4 up as PTZOptics).
Needs the exported model (run tools/env_check.py once) to cut the people out of the sample photo.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from _common import parse_host_port

from trackxen.control.lens import Lens
from trackxen.sim.fake_camera import ptzoptics_settings
from trackxen.sim.virtual_camera import VirtualCamera, default_scene, run_forever

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolo26n-pose-b1.onnx"


def main(args) -> int:
    if not MODEL.exists():
        print(f"missing {MODEL}; run tools/env_check.py first")
        return 1
    lens, camera_args = None, None
    if args.ptzoptics:  # the video stays NDI; the control is the PTZOptics kind
        from trackxen.core.camera_models import MODELS

        lens_cfg = MODELS["ptzoptics_g2"]["lens"]
        lens = Lens(lens_cfg["hfov_wide"], lens_cfg["hfov_tele"], lens_cfg["zoom_max"])
        camera_args = ptzoptics_settings()
        args.freed_to = None
    camera = VirtualCamera(default_scene(MODEL, ppd=args.ppd), ndi_name=args.name, lens=lens,
                           freed_target=parse_host_port(args.freed_to, 40001) if args.freed_to else None,
                           fps=args.fps, video_delay=args.video_delay, command_delay=args.command_delay,
                           camera_args=camera_args)
    print(f"virtual {'PTZOptics G2' if args.ptzoptics else 'P400'} '{args.name}': "
          f"{'plain ' if args.ptzoptics else ''}VISCA 127.0.0.1:{args.visca_port}, "
          f"FreeD -> {args.freed_to or 'off'}, video delay {args.video_delay * 1000:.0f} ms, "
          f"command delay {args.command_delay * 1000:.0f} ms. Ctrl+C to stop.")
    asyncio.run(run_forever(camera, "127.0.0.1", args.visca_port))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", type=int, default=1, help="number k: NDI \"TRACKXEN SIM k\", VISCA 52380+k, FreeD 40000+k")
    p.add_argument("--name")
    p.add_argument("--visca-port", type=int)
    p.add_argument("--freed-to")
    p.add_argument("--ptzoptics", action="store_true", help="behave like a PTZOptics G2 (plain VISCA, no FreeD)")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--video-delay", type=float, default=0.08, help="seconds, like a real video pipeline")
    p.add_argument("--command-delay", type=float, default=0.03, help="seconds before a command takes effect")
    p.add_argument("--ppd", type=float, default=40.0, help="scene detail, pixels per degree")
    a = p.parse_args()
    a.name = a.name or f"TRACKXEN SIM {a.camera}"
    a.visca_port = a.visca_port or 52380 + a.camera
    a.freed_to = a.freed_to or f"127.0.0.1:{40000 + a.camera}"
    try:
        sys.exit(main(a))
    except KeyboardInterrupt:
        sys.exit(0)

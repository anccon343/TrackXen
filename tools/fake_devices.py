"""Run pretend devices on this PC, to try the lab tools without hardware.

  python tools/fake_devices.py --freed-to 127.0.0.1:40001

Starts a fake camera (VISCA on 127.0.0.1:52381, FreeD to --freed-to), a fake MR18
(127.0.0.1:10024) and a fake vMix (127.0.0.1:8099). Stop with Ctrl+C.

With the simulator (tools/sim_camera.py already provides the camera):
  python tools/fake_devices.py --no-camera --vmix-input "TRACKXEN SIM 1" --cycle 8
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from _common import parse_host_port

from trackxen.sim.fake_camera import FakeCamera
from trackxen.sim.fake_mixer import FakeMixer
from trackxen.sim.fake_vmix import FakeVmix


async def main(args) -> None:
    freed_to = parse_host_port(args.freed_to, 40001) if args.freed_to else None
    camera = None if args.no_camera else FakeCamera(freed_target=freed_to)
    mixer, vmix_ = FakeMixer(), FakeVmix(cycle_seconds=args.cycle, first_title=args.vmix_input)
    if camera:
        print(f"fake camera: VISCA 127.0.0.1:{await camera.start(port=args.visca_port)}"
              + (f", FreeD -> {freed_to[0]}:{freed_to[1]}" if freed_to else ""))
    print(f"fake mixer:  127.0.0.1:{await mixer.start(port=args.mixer_port)}")
    print(f"fake vMix:   127.0.0.1:{await vmix_.start(port=args.vmix_port)}")
    try:
        await asyncio.Event().wait()
    finally:
        if camera:
            camera.stop()
        mixer.stop()
        vmix_.stop()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--freed-to", help="host:port to send FreeD to, e.g. 127.0.0.1:40001")
    p.add_argument("--visca-port", type=int, default=52381)
    p.add_argument("--mixer-port", type=int, default=10024)
    p.add_argument("--vmix-port", type=int, default=8099)
    p.add_argument("--no-camera", action="store_true", help="no fake camera (the simulator provides one)")
    p.add_argument("--vmix-input", default="", help="title of vMix input 1, e.g. the simulator's NDI source")
    p.add_argument("--cycle", type=float, default=3.0, help="seconds between vMix tally changes")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        sys.exit(0)

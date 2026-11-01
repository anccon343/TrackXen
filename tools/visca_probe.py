"""Talk to a camera over VISCA over IP: read its position, move it, and measure its speed steps.

Read-only by default. Anything that moves the camera needs --yes, and every run
ends with a stop command (also on Ctrl+C).

  python tools/visca_probe.py 192.168.1.50 info
  python tools/visca_probe.py 192.168.1.50 move --pan right --speed 3 --seconds 2 --yes
  python tools/visca_probe.py 192.168.1.50 sweep --axis pan --speeds 1-24 --freed-port 40001 --yes
  python tools/visca_probe.py 192.168.1.50 stop
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from _common import results_path, write_csv

from trackxen.devices import freed, visca


def parse_speeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


class FreeDTracker:
    """Keeps the latest FreeD position and the time the camera last moved."""

    def __init__(self):
        self.latest: freed.FreeDPosition | None = None
        self.count = 0
        self.first_motion: float | None = None
        self._armed_at: tuple[float, float, float] | None = None  # (time, pan, tilt)

    def on_position(self, pos: freed.FreeDPosition, addr, t: float) -> None:
        self.latest, self.count = pos, self.count + 1
        if self._armed_at and self.first_motion is None:
            _, pan0, tilt0 = self._armed_at
            if abs(pos.pan - pan0) > 0.02 or abs(pos.tilt - tilt0) > 0.02:
                self.first_motion = t

    def arm(self) -> float:
        """Start waiting for motion; returns the arm time."""
        now = time.monotonic()
        pan, tilt = (self.latest.pan, self.latest.tilt) if self.latest else (0.0, 0.0)
        self._armed_at, self.first_motion = (now, pan, tilt), None
        return now


async def cmd_info(client: visca.ViscaClient, args) -> None:
    try:
        power = await client.inquiry(visca.inq_power())
        print(f"power: {'on' if power[:1] == b'\x02' else power.hex(' ')}")
    except visca.ViscaError as e:
        print(f"power inquiry failed: {e}")
    pan, tilt = await client.pan_tilt_position()
    print(f"pan/tilt (VISCA units): {pan} / {tilt}")
    print(f"zoom (VISCA units): {await client.zoom_position()}")
    try:
        focus = visca.decode_position(await client.inquiry(visca.inq_focus_position()))
        print(f"focus (VISCA units): {focus}")
    except visca.ViscaError as e:
        print(f"focus inquiry failed: {e}")


async def cmd_move(client: visca.ViscaClient, args) -> None:
    pan = {"left": visca.PanDir.LEFT, "right": visca.PanDir.RIGHT}.get(args.pan, visca.PanDir.STOP)
    tilt = {"up": visca.TiltDir.UP, "down": visca.TiltDir.DOWN}.get(args.tilt, visca.TiltDir.STOP)
    before = await client.pan_tilt_position()
    await client.command(visca.pan_tilt_drive(args.speed, args.speed, pan, tilt))
    await asyncio.sleep(args.seconds)
    await client.command(visca.pan_tilt_stop())
    await asyncio.sleep(0.5)
    after = await client.pan_tilt_position()
    print(f"moved from {before} to {after} (VISCA units)")


async def cmd_zoom(client: visca.ViscaClient, args) -> None:
    await client.command(visca.zoom_direct(args.to), wait_completion=True)
    print(f"zoom now {await client.zoom_position()}")


async def cmd_preset(client: visca.ViscaClient, args) -> None:
    await client.command(visca.preset_recall(args.recall), wait_completion=True)
    print(f"preset {args.recall} recalled; pan/tilt {await client.pan_tilt_position()}")


async def cmd_sweep(client: visca.ViscaClient, args) -> None:
    """Drive each speed step for a moment and measure how fast the camera really turned."""
    tracker = FreeDTracker()
    transport = None
    if args.freed_port:
        transport = await freed.listen(args.freed_port, tracker.on_position)
        await asyncio.sleep(0.5)
        if not tracker.count:
            print("warning: no FreeD packets yet; measuring with VISCA inquiries only")
    rows = []
    direction = 1
    try:
        for speed in parse_speeds(args.speeds):
            await asyncio.sleep(args.settle)
            p0 = await client.pan_tilt_position()
            f0 = tracker.latest
            if args.axis == "pan":
                cmd = visca.pan_tilt_drive(speed, 1, visca.PanDir.RIGHT if direction > 0 else visca.PanDir.LEFT,
                                           visca.TiltDir.STOP)
            else:
                cmd = visca.pan_tilt_drive(1, speed, visca.PanDir.STOP,
                                           visca.TiltDir.UP if direction > 0 else visca.TiltDir.DOWN)
            sent = tracker.arm()
            await client.command(cmd)
            await asyncio.sleep(args.seconds)
            await client.command(visca.pan_tilt_stop())
            elapsed = time.monotonic() - sent
            await asyncio.sleep(args.settle)
            p1 = await client.pan_tilt_position()
            f1 = tracker.latest
            axis = 0 if args.axis == "pan" else 1
            units = abs(p1[axis] - p0[axis])
            deg = abs((f1.pan - f0.pan) if axis == 0 else (f1.tilt - f0.tilt)) if f0 and f1 else None
            latency = (tracker.first_motion - sent) * 1000 if tracker.first_motion else None
            rows.append([speed, round(args.seconds, 3), units, round(units / args.seconds, 1),
                         round(deg, 4) if deg is not None else "",
                         round(deg / args.seconds, 3) if deg is not None else "",
                         round(units / deg, 3) if deg else "",
                         round(latency, 1) if latency is not None else "",
                         round(elapsed, 3)])
            print(f"speed {speed:2d}: {units:6d} units ({units / args.seconds:8.1f}/s)"
                  + (f", {deg:8.3f} deg ({deg / args.seconds:7.3f} deg/s)" if deg is not None else "")
                  + (f", motion after {latency:.0f} ms" if latency is not None else ""))
            direction = -direction
    finally:
        if transport:
            transport.close()
    path = results_path(f"speed-sweep-{args.axis}")
    write_csv(path, ["speed", "seconds", "visca_units", "units_per_s", "freed_deg", "deg_per_s",
                     "units_per_deg", "motion_latency_ms", "elapsed_s"], rows)
    print(f"saved {path}")


COMMANDS = {"info": (cmd_info, False), "move": (cmd_move, True), "zoom": (cmd_zoom, True),
            "preset": (cmd_preset, True), "sweep": (cmd_sweep, True), "stop": (None, False)}


async def main(args) -> int:
    handler, moves = COMMANDS[args.command]
    if moves and not args.yes:
        print("This command moves the camera. Add --yes to confirm it's safe to move it now.")
        return 2
    client = visca.ViscaClient(args.host, args.port, timeout=args.timeout, use_header=not args.no_header)
    await client.connect()
    try:
        if handler:
            await handler(client, args)
        return 0
    except visca.ViscaError as e:
        print(f"error: {e}")
        return 1
    finally:
        try:
            await client.stop_all()
        except visca.ViscaError:
            pass
        client.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--port", type=int, default=visca.VISCA_PORT)
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--no-header", action="store_true", help="send bare VISCA without the IP header")
    p.add_argument("--yes", action="store_true", help="confirm that the camera may move")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="read power, pan/tilt, zoom and focus (no movement)")
    sub.add_parser("stop", help="stop pan, tilt and zoom")
    m = sub.add_parser("move", help="drive pan/tilt for a few seconds")
    m.add_argument("--pan", choices=["left", "right"])
    m.add_argument("--tilt", choices=["up", "down"])
    m.add_argument("--speed", type=int, default=3)
    m.add_argument("--seconds", type=float, default=1.0)
    z = sub.add_parser("zoom", help="zoom to a position")
    z.add_argument("--to", type=int, required=True)
    pr = sub.add_parser("preset", help="recall a preset")
    pr.add_argument("--recall", type=int, required=True)
    s = sub.add_parser("sweep", help="measure every speed step")
    s.add_argument("--axis", choices=["pan", "tilt"], default="pan")
    s.add_argument("--speeds", default="1-24", help="e.g. 1-24 or 1,2,3,8")
    s.add_argument("--seconds", type=float, default=1.5, help="drive time per step")
    s.add_argument("--settle", type=float, default=0.6, help="wait after each stop")
    s.add_argument("--freed-port", type=int, help="also measure with FreeD on this UDP port")
    return p


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(build_parser().parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

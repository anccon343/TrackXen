"""Talk to a camera over VISCA over IP: read its position, move it, and measure its speed steps.

Read-only by default. Anything that moves the camera needs --yes and ends with a stop
command (also on Ctrl+C). `info` only reads: it sends no stop, so it never halts a move made by
vMix, an operator or TrackXen.

  python tools/visca_probe.py 192.168.1.50 info
  python tools/visca_probe.py 192.168.1.50 ramp      (does the camera have Sony's ramp curve / pan-tilt slow?)
  python tools/visca_probe.py 192.168.1.50 move --pan right --speed 3 --seconds 2 --yes
  python tools/visca_probe.py 192.168.1.50 sweep --axis pan --speeds 1-24 --freed-port 40001 --yes
  python tools/visca_probe.py 192.168.1.50 stop

PTZOptics G2 (plain VISCA, UDP 1259 or TCP 5678, no FreeD):

  python tools/visca_probe.py 192.168.1.60 --transport udp info
  python tools/visca_probe.py 192.168.1.60 --transport udp calibrate --yes     (units per degree, from the end stops)
  python tools/visca_probe.py 192.168.1.60 --transport udp zoom-range --yes
  python tools/visca_probe.py 192.168.1.60 --transport udp sweep --axis tilt --speeds 1-20 --units-per-degree 13.3 --yes
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


# Sony's read-only inquiries for how the camera itself ramps and how slow it can turn (BRC-X400 VISCA list): ramp
# curve 8x 09 06 31 FF (set with 8x 01 06 31 0p FF) and pan-tilt slow 8x 09 06 44 FF (8x 01 06 44 0p FF, 2 = on,
# 3 = off). Neither the P400 nor the G2 documents them; a camera without them answers 90 60 02 FF (syntax error).
RAMP_INQUIRIES = [
    ("ramp curve", bytes([0x81, 0x09, 0x06, 0x31, 0xFF]), str),
    ("pan-tilt slow", bytes([0x81, 0x09, 0x06, 0x44, 0xFF]), lambda v: {2: "on", 3: "off"}.get(v, f"value {v}")),
]


async def cmd_ramp(client: visca.ViscaClient, args) -> None:
    """Ask for the ramp curve and pan-tilt slow mode. Only inquiries: nothing is set, nothing moves."""
    rows = []
    for name, msg, meaning in RAMP_INQUIRIES:
        try:
            data = await client.inquiry(msg)
            if len(data) == 1 and data[0] <= 0x0F:
                answer = meaning(data[0])
            else:
                answer = f"unexpected reply {data.hex(' ') or '(empty)'}"
            reply = data.hex(" ")
        except visca.ViscaError as e:
            text = str(e)
            answer = ("no reply (not supported, or the camera ignores it)" if text.startswith("no reply")
                      else f"not supported ({text.rsplit(': ', 1)[-1]})")
            reply = ""
        print(f"{name} ({msg.hex(' ')}): {answer}")
        rows.append([name, msg.hex(" "), reply, answer])
    path = results_path("visca-ramp")
    write_csv(path, ["inquiry", "sent", "reply_data", "answer"], rows)
    print(f"saved {path}")


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
            if deg is None and args.units_per_degree:  # no FreeD (PTZOptics): degrees from the calibration
                deg = units / args.units_per_degree
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
    write_csv(path, ["speed", "seconds", "visca_units", "units_per_s", "deg", "deg_per_s",
                     "units_per_deg", "motion_latency_ms", "elapsed_s"], rows)
    print(f"saved {path}")
    if all(r[5] != "" for r in rows):  # the camera's speed table, for Settings -> Cameras (pan_speeds/tilt_speeds)
        print(f"{args.axis}_speeds: {[r[5] for r in rows]}")


async def _drive_to_end(client: visca.ViscaClient, cmd: bytes, axis: int, timeout: float = 40.0) -> int:
    """Drive until the position stops changing (the end stop or a limit); returns that position."""
    await client.command(cmd)
    last, still_since, start = None, None, time.monotonic()
    try:
        while time.monotonic() - start < timeout:
            await asyncio.sleep(0.1)
            pos = (await client.pan_tilt_position())[axis]
            if pos != last:
                last, still_since = pos, time.monotonic()
            elif time.monotonic() - still_since > 0.6:
                return pos
        raise visca.ViscaError(f"still moving after {timeout:.0f} s")
    finally:
        await client.command(visca.pan_tilt_stop())


async def cmd_calibrate(client: visca.ViscaClient, args) -> None:
    """Units per degree from the end stops: the camera turns to both pan ends and both tilt ends (data sheet
    angles: PTZOptics G2 pan ±170°, tilt -30°..+90°), then goes back. Clear the camera's pan/tilt limits
    first, or the limits are measured instead of the ends."""
    home = await client.pan_tilt_position()
    s, t = args.speed, min(args.speed, args.tilt_speed_max)  # (a PTZOptics refuses tilt speeds above 20)
    left = await _drive_to_end(client, visca.pan_tilt_drive(s, 1, visca.PanDir.LEFT, visca.TiltDir.STOP), 0)
    right = await _drive_to_end(client, visca.pan_tilt_drive(s, 1, visca.PanDir.RIGHT, visca.TiltDir.STOP), 0)
    down = await _drive_to_end(client, visca.pan_tilt_drive(1, t, visca.PanDir.STOP, visca.TiltDir.DOWN), 1)
    up = await _drive_to_end(client, visca.pan_tilt_drive(1, t, visca.PanDir.STOP, visca.TiltDir.UP), 1)
    await client.command(visca.pan_tilt_absolute(home[0], home[1], s, t))
    pan_units, tilt_units = abs(right - left) / args.pan_range, abs(up - down) / args.tilt_range
    print(f"pan ends {left} .. {right} over {args.pan_range:g} deg: {pan_units:.3f} units per degree"
          f"{' (right is negative: the positions count against the drive, which pan_sign cannot fix)' if right < left else ''}")
    print(f"tilt ends {down} .. {up} over {args.tilt_range:g} deg: {tilt_units:.3f} units per degree"
          f"{' (up is negative: the positions count against the drive, which tilt_sign cannot fix)' if up < down else ''}")
    print(f"Settings -> Cameras: units_per_degree {pan_units:.3f}, tilt_units_per_degree {tilt_units:.3f}")
    path = results_path("calibrate")
    write_csv(path, ["pan_left", "pan_right", "pan_range_deg", "pan_units_per_deg", "tilt_down", "tilt_up",
                     "tilt_range_deg", "tilt_units_per_deg"],
              [[left, right, args.pan_range, round(pan_units, 4), down, up, args.tilt_range, round(tilt_units, 4)]])
    print(f"saved {path}")


async def cmd_zoom_range(client: visca.ViscaClient, args) -> None:
    """The zoom position at full wide and full tele (Settings -> Cameras: lens zoom_max)."""
    start = await client.zoom_position()
    ends = []
    for cmd in (visca.zoom_wide(7), visca.zoom_tele(7)):
        await client.command(cmd)
        last, still_since, t0 = None, time.monotonic(), time.monotonic()
        while time.monotonic() - t0 < 20:
            await asyncio.sleep(0.1)
            pos = await client.zoom_position()
            if pos != last:
                last, still_since = pos, time.monotonic()
            elif time.monotonic() - still_since > 0.6:
                break
        await client.command(visca.zoom_stop())
        ends.append(last)
    await client.command(visca.zoom_direct(start))
    print(f"zoom: wide {ends[0]}, tele {ends[1]} (lens zoom_max {ends[1]})")
    path = results_path("zoom-range")
    write_csv(path, ["zoom_wide", "zoom_tele"], [ends])
    print(f"saved {path}")


COMMANDS = {"info": (cmd_info, False), "ramp": (cmd_ramp, False), "move": (cmd_move, True), "zoom": (cmd_zoom, True),
            "preset": (cmd_preset, True), "sweep": (cmd_sweep, True), "calibrate": (cmd_calibrate, True),
            "zoom-range": (cmd_zoom_range, True), "stop": (None, False)}
DEFAULT_PORTS = {"sony": visca.VISCA_PORT, "udp": visca.PLAIN_UDP_PORT, "tcp": visca.TCP_PORT}


async def main(args) -> int:
    handler, moves = COMMANDS[args.command]
    if moves and not args.yes:
        print("This command moves the camera. Add --yes to confirm it's safe to move it now.")
        return 2
    transport = "udp" if args.no_header and args.transport == "sony" else args.transport
    client = visca.open_client(args.host, args.port or DEFAULT_PORTS[transport], transport, timeout=args.timeout)
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
            if moves or args.command == "stop":
                await client.stop_all()
        except visca.ViscaError:
            pass
        client.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--transport", choices=["sony", "udp", "tcp"], default="sony",
                   help="sony: VISCA over IP with header, UDP 52381 (BirdDog); udp / tcp: plain VISCA (PTZOptics 1259 / 5678)")
    p.add_argument("--port", type=int, help="default: 52381, 1259 or 5678 by --transport")
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--no-header", action="store_true", help="the same as --transport udp")
    p.add_argument("--yes", action="store_true", help="confirm that the camera may move")
    yes = argparse.ArgumentParser(add_help=False)  # --yes also after the command (move ... --yes)
    yes.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="confirm that the camera may move")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="read power, pan/tilt, zoom and focus (no movement)")
    sub.add_parser("ramp", help="ask for Sony's ramp curve and pan-tilt slow mode (only reads, no movement)")
    sub.add_parser("stop", help="stop pan, tilt and zoom")
    m = sub.add_parser("move", parents=[yes], help="drive pan/tilt for a few seconds")
    m.add_argument("--pan", choices=["left", "right"])
    m.add_argument("--tilt", choices=["up", "down"])
    m.add_argument("--speed", type=int, default=3)
    m.add_argument("--seconds", type=float, default=1.0)
    z = sub.add_parser("zoom", parents=[yes], help="zoom to a position")
    z.add_argument("--to", type=int, required=True)
    pr = sub.add_parser("preset", parents=[yes], help="recall a preset")
    pr.add_argument("--recall", type=int, required=True)
    s = sub.add_parser("sweep", parents=[yes], help="measure every speed step")
    s.add_argument("--axis", choices=["pan", "tilt"], default="pan")
    s.add_argument("--speeds", default="1-24", help="e.g. 1-24 or 1,2,3,8")
    s.add_argument("--seconds", type=float, default=1.5, help="drive time per step")
    s.add_argument("--settle", type=float, default=0.6, help="wait after each stop")
    s.add_argument("--freed-port", type=int, help="also measure with FreeD on this UDP port")
    s.add_argument("--units-per-degree", type=float, help="without FreeD: degrees from VISCA units (from calibrate)")
    c = sub.add_parser("calibrate", parents=[yes], help="units per degree from the pan and tilt end stops (the camera turns fully)")
    c.add_argument("--speed", type=int, default=12, help="drive speed step")
    c.add_argument("--pan-range", type=float, default=340.0, help="degrees between the pan ends (PTZOptics G2: 340)")
    c.add_argument("--tilt-range", type=float, default=120.0, help="degrees between the tilt ends (PTZOptics G2: 120)")
    c.add_argument("--tilt-speed-max", type=int, default=20, help="the camera's fastest tilt step (PTZOptics: 20)")
    sub.add_parser("zoom-range", parents=[yes], help="zoom positions at full wide and full tele")
    return p


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(build_parser().parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

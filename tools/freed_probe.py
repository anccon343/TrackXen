"""Listen for FreeD position data and report rate, values and timing.

  python tools/freed_probe.py --port 40001 --seconds 10 --csv

Set the camera's FreeD destination in BirdUI (CamControl > FreeD) to this PC's IP and the port.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from _common import results_path, write_csv

from trackxen.devices import freed


async def main(args) -> int:
    rows: list[list] = []
    times: list[float] = []
    senders: set[str] = set()
    bad = 0

    def on_position(pos: freed.FreeDPosition, addr, t: float) -> None:
        times.append(t)
        senders.add(f"{addr[0]}:{addr[1]}")
        rows.append([round(t, 4), pos.camera_id, round(pos.pan, 4), round(pos.tilt, 4), round(pos.roll, 4),
                     pos.zoom, pos.focus, pos.spare])

    def on_bad(data: bytes) -> None:
        nonlocal bad
        bad += 1

    transport = await freed.listen(args.port, on_position, verify=not args.no_verify, on_bad=on_bad)
    print(f"listening for FreeD on UDP {args.port} for {args.seconds:g} s ...")
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            await asyncio.sleep(1.0)
            if rows:
                last = rows[-1]
                print(f"  {len(rows):6d} packets  pan {last[2]:9.3f}  tilt {last[3]:8.3f}  "
                      f"zoom {last[5]:8d}  focus {last[6]:8d}")
            else:
                print("  no packets yet")
    finally:
        transport.close()

    if bad:
        print(f"{bad} packets failed to decode (try --no-verify if they're all checksum errors)")
    if len(times) < 2:
        print("not enough packets to measure the rate")
        return 1
    gaps = [b - a for a, b in zip(times, times[1:])]
    rate = (len(times) - 1) / (times[-1] - times[0])
    print(f"senders: {', '.join(sorted(senders))}")
    print(f"rate: {rate:.1f} packets/s, gap median {statistics.median(gaps) * 1000:.1f} ms, "
          f"max {max(gaps) * 1000:.1f} ms")
    if args.csv:
        path = results_path(f"freed-{args.port}")
        write_csv(path, ["t_monotonic", "camera_id", "pan_deg", "tilt_deg", "roll_deg", "zoom_raw",
                         "focus_raw", "spare"], rows)
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=40001)
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--csv", action="store_true", help="save every packet to lab-results/")
    p.add_argument("--no-verify", action="store_true", help="accept packets with a bad checksum")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

"""Show live input levels and channel names from a Midas MR18 / Behringer X Air.

  python tools/xair_meters.py                 # find the mixer automatically
  python tools/xair_meters.py 192.168.1.40 --seconds 30 --csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from _common import results_path, write_csv

from trackxen.integrations import xair


def bar(db: float, width: int = 30, floor: float = -60.0) -> str:
    filled = int(max(0.0, min(1.0, (db - floor) / -floor)) * width)
    return "#" * filled + "." * (width - filled)


async def main(args) -> int:
    host = args.host
    if not host:
        print("looking for MR18 / X Air mixers ...")
        mixers = await xair.discover()
        for m in mixers:
            print(f"  found {m.model} '{m.name}' at {m.ip} (firmware {m.firmware})")
        if not mixers:
            print("no mixer answered; give its IP address as an argument")
            return 1
        host = mixers[0].ip

    names = {ch: f"Ch {ch}" for ch in range(1, xair.INPUT_CHANNELS + 1)}
    muted: dict[int, bool] = {}
    latest: list[float] = []
    rows: list[list] = []
    count = 0

    def on_meters(levels: list[float]) -> None:
        nonlocal latest, count
        latest, count = levels, count + 1
        if args.csv:
            rows.append([round(time.monotonic(), 3)] + [round(v, 2) for v in levels[:xair.INPUT_CHANNELS]])

    def on_value(address: str, values: list) -> None:
        parts = address.split("/")
        if len(parts) >= 4 and parts[1] == "ch" and values:
            ch = int(parts[2])
            if address.endswith("config/name") and values[0]:
                names[ch] = str(values[0])
            elif address.endswith("mix/on"):
                muted[ch] = not bool(values[0])

    client = xair.XAirClient(host, on_meters=on_meters, on_value=on_value,
                             send_update_rate=args.update_rate)
    await client.connect()
    client.request_channel_info()
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            await asyncio.sleep(0.5)
            if not latest:
                print("waiting for meters ...")
                continue
            print(f"\n{count} meter updates ({count / (time.monotonic() - start):.0f}/s)")
            for ch in range(1, xair.INPUT_CHANNELS + 1):
                db = latest[ch - 1]
                flag = " MUTED" if muted.get(ch) else ""
                print(f"  {ch:2d} {names[ch][:12]:12s} {db:6.1f} dB |{bar(db)}|{flag}")
    finally:
        client.close()
    if args.csv and rows:
        path = results_path("xair-meters")
        write_csv(path, ["t_monotonic"] + [names[ch] for ch in range(1, xair.INPUT_CHANNELS + 1)], rows)
        print(f"saved {path}")
    return 0 if count else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", nargs="?")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--csv", action="store_true", help="save input levels to lab-results/")
    p.add_argument("--update-rate", action="store_true", help="also send /-local/updrate 1 like Companion")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

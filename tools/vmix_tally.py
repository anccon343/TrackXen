"""Show vMix's inputs and print every tally change (Program / Preview).

  python tools/vmix_tally.py 192.168.1.20 --seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from trackxen.integrations import vmix


async def main(args) -> int:
    query = vmix.VmixClient(args.host, args.port)
    try:
        await query.connect()
    except (OSError, TimeoutError) as e:
        print(f"can't reach vMix at {args.host}:{args.port}: {e}")
        return 1
    inputs = vmix.parse_inputs(await query.request_xml())
    query.close()
    titles = {i.number: i.title for i in inputs}
    print("vMix inputs:")
    for i in inputs:
        print(f"  {i.number:3d}  {i.type:12s}  {i.title}")

    events = vmix.VmixClient(args.host, args.port)
    await events.connect()

    async def watch() -> None:
        async for tally in events.tally_events():
            program = [n for n, t in enumerate(tally, 1) if t is vmix.Tally.PROGRAM]
            preview = [n for n, t in enumerate(tally, 1) if t is vmix.Tally.PREVIEW]
            fmt = lambda nums: ", ".join(f"{n} {titles.get(n, '')}".strip() for n in nums) or "-"
            print(f"PROGRAM: {fmt(program)}   PREVIEW: {fmt(preview)}")

    try:
        await asyncio.wait_for(watch(), args.seconds)
    except TimeoutError:
        pass
    finally:
        events.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--port", type=int, default=vmix.VMIX_PORT)
    p.add_argument("--seconds", type=float, default=60.0)
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

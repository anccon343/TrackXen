"""Find everything the tracker can use on the network: NDI sources, BirdDog cameras, MR18 / X Air
mixers and vMix.

  python tools/discover.py
  python tools/discover.py --vmix 192.168.1.20
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys

from trackxen.devices.birddog_rest import BirdDogRest
from trackxen.devices.ndi_in import find_sources
from trackxen.integrations import vmix, xair


def resolve(host: str) -> str | None:
    for name in (f"{host}.local", host):
        try:
            return socket.getaddrinfo(name, None, socket.AF_INET)[0][4][0]
        except OSError:
            continue
    return None


async def check_birddog(ip: str) -> str:
    try:
        async with BirdDogRest(ip, timeout=1.5) as rest:
            about = await rest.about()
        if isinstance(about, dict):
            return (f"BirdDog: firmware {about.get('FirmwareVersion', '?')}, "
                    f"status {about.get('Status', '?')}, serial {about.get('SerialNumber', '?')}")
        return f"BirdDog: {about}"
    except Exception:  # noqa: BLE001 - not a BirdDog or not reachable
        return "no BirdDog REST API on port 8080"


async def main(args) -> int:
    print("NDI sources:")
    sources = await asyncio.to_thread(find_sources, args.timeout)
    for s in sources:
        ip = await asyncio.to_thread(resolve, s.host_name)
        detail = await check_birddog(ip) if ip else "host name doesn't resolve"
        print(f"  {s.name:40s} {ip or '?':16s} {detail}")
    if not sources:
        print("  none found")

    print("MR18 / X Air mixers:")
    mixers = await xair.discover(timeout=1.5)
    for m in mixers:
        print(f"  {m.model} '{m.name}' at {m.ip} (firmware {m.firmware})")
    if not mixers:
        print("  none answered")

    host = args.vmix or "127.0.0.1"
    client = vmix.VmixClient(host, timeout=1.5)
    try:
        await client.connect()
        inputs = vmix.parse_inputs(await client.request_xml())
        print(f"vMix at {host}: {len(inputs)} inputs")
    except (OSError, TimeoutError, ConnectionError):
        print(f"vMix: not reachable at {host}:{vmix.VMIX_PORT}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vmix", help="vMix PC address (default: this PC)")
    p.add_argument("--timeout", type=float, default=4.0, help="seconds to look for NDI sources")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

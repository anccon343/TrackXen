"""A pretend MR18: answers /xinfo, channel queries and streams /meters/1 like the real mixer.

Channel 1 "talks" in bursts so audio-follow logic has something to react to.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time

from trackxen.integrations import xair

SILENCE_DB = -90.0


def meters1_blob(levels_db: list[float]) -> bytes:
    values = [max(-32768, min(32767, round(v * 256))) for v in levels_db]
    return struct.pack("<i", len(values)) + struct.pack(f"<{len(values)}h", *values)


class FakeMixer(asyncio.DatagramProtocol):
    def __init__(self, name: str = "FAKE-MR18", model: str = "MR18"):
        self.name, self.model = name, model
        self.transport: asyncio.DatagramTransport | None = None
        self.meter_subscribers: dict[tuple[str, int], float] = {}  # addr -> expiry time
        self.received: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self, host: str = "127.0.0.1", port: int = xair.XAIR_PORT) -> int:
        from trackxen.devices.udp import open_udp

        self.transport, _ = await open_udp(lambda: self, local=(host, port))
        self._task = asyncio.create_task(self._stream())
        return self.transport.get_extra_info("sockname")[1]

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.transport:
            self.transport.close()

    def _send(self, addr, address: str, *args) -> None:
        if self.transport:
            self.transport.sendto(xair.osc_message(address, *args), addr)

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            address, args = xair.parse_osc(data)
        except Exception:
            return
        self.received.append(address)
        if address == "/xinfo":
            self._send(addr, "/xinfo", "127.0.0.1", self.name, self.model, "1.22")
        elif address == "/meters" and args and args[0] == "/meters/1":
            self.meter_subscribers[addr] = time.monotonic() + 10.0
        elif address.startswith("/ch/") and not args:
            ch = int(address.split("/")[2])
            if address.endswith("config/name"):
                self._send(addr, address, f"Mic {ch}")
            elif address.endswith("mix/on"):
                self._send(addr, address, 1)
            elif address.endswith("mix/fader"):
                self._send(addr, address, 0.75)

    def levels(self, now: float) -> list[float]:
        levels = [SILENCE_DB] * xair.METERS1_COUNT
        talking = math.sin(now * 0.8) > 0  # channel 1 talks for ~4 s, pauses ~4 s
        levels[0] = -18.0 + 6.0 * math.sin(now * 9.0) if talking else -70.0
        levels[1] = -65.0
        return levels

    async def _stream(self, hz: float = 20.0) -> None:
        while True:
            await asyncio.sleep(1 / hz)
            now = time.monotonic()
            self.meter_subscribers = {a: t for a, t in self.meter_subscribers.items() if t > now}
            if self.meter_subscribers:
                blob = meters1_blob(self.levels(now))
                for addr in self.meter_subscribers:
                    self._send(addr, "/meters/1", blob)

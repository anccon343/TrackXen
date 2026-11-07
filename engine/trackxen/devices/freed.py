"""FreeD "D1" position messages, as sent by BirdDog cameras (BirdUI > CamControl > FreeD).

Layout (29 bytes): 0xD1, camera id, pan, tilt, roll (signed 24-bit, 1/32768 degree),
X, Y, Z (signed 24-bit, 1/64 mm), zoom, focus (raw 24-bit lens values), 2 spare bytes
and a checksum (0x40 minus the sum of the first 28 bytes). The P400's zoom/focus
scaling is vendor-specific and gets calibrated against the real field of view.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

D1 = 0xD1
PACKET_SIZE = 29
ANGLE_UNITS = 32768.0  # units per degree
POSITION_UNITS = 64.0  # units per millimetre


@dataclass(frozen=True)
class FreeDPosition:
    camera_id: int
    pan: float  # degrees
    tilt: float
    roll: float
    x: float  # millimetres
    y: float
    z: float
    zoom: int  # raw lens value
    focus: int
    spare: int


def checksum(data: bytes) -> int:
    return (0x40 - sum(data)) & 0xFF


def _s24(b: bytes) -> int:
    v = int.from_bytes(b, "big")
    return v - (1 << 24) if v & 0x800000 else v


def _u24(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _to_s24(value: int) -> bytes:
    return (value & 0xFFFFFF).to_bytes(3, "big")


def parse(packet: bytes, verify: bool = True) -> FreeDPosition:
    if len(packet) < PACKET_SIZE or packet[0] != D1:
        raise ValueError(f"not a FreeD D1 packet ({len(packet)} bytes)")
    if verify and checksum(packet[:28]) != packet[28]:
        raise ValueError("FreeD checksum mismatch")
    return FreeDPosition(
        camera_id=packet[1],
        pan=_s24(packet[2:5]) / ANGLE_UNITS,
        tilt=_s24(packet[5:8]) / ANGLE_UNITS,
        roll=_s24(packet[8:11]) / ANGLE_UNITS,
        x=_s24(packet[11:14]) / POSITION_UNITS,
        y=_s24(packet[14:17]) / POSITION_UNITS,
        z=_s24(packet[17:20]) / POSITION_UNITS,
        zoom=_u24(packet[20:23]),
        focus=_u24(packet[23:26]),
        spare=int.from_bytes(packet[26:28], "big"),
    )


def encode(pos: FreeDPosition) -> bytes:
    """Build a D1 packet (used by tests and the fake camera)."""
    body = (
        bytes([D1, pos.camera_id & 0xFF])
        + _to_s24(round(pos.pan * ANGLE_UNITS))
        + _to_s24(round(pos.tilt * ANGLE_UNITS))
        + _to_s24(round(pos.roll * ANGLE_UNITS))
        + _to_s24(round(pos.x * POSITION_UNITS))
        + _to_s24(round(pos.y * POSITION_UNITS))
        + _to_s24(round(pos.z * POSITION_UNITS))
        + (pos.zoom & 0xFFFFFF).to_bytes(3, "big")
        + (pos.focus & 0xFFFFFF).to_bytes(3, "big")
        + (pos.spare & 0xFFFF).to_bytes(2, "big")
    )
    return body + bytes([checksum(body)])


PositionCallback = Callable[[FreeDPosition, tuple[str, int], float], None]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, callback: PositionCallback, verify: bool, on_bad: Callable[[bytes], None] | None):
        self.callback, self.verify, self.on_bad = callback, verify, on_bad

    def datagram_received(self, data: bytes, addr) -> None:
        received = time.monotonic()
        try:
            pos = parse(data, self.verify)
        except ValueError:
            if self.on_bad:
                self.on_bad(data)
            return
        self.callback(pos, addr, received)


async def listen(port: int, callback: PositionCallback, host: str = "0.0.0.0",
                 verify: bool = True, on_bad: Callable[[bytes], None] | None = None
                 ) -> asyncio.DatagramTransport:
    """Receive FreeD packets on a UDP port; `callback(position, sender, monotonic_time)`."""
    from trackxen.devices.udp import open_udp

    transport, _ = await open_udp(lambda: _Protocol(callback, verify, on_bad), local=(host, port))
    return transport

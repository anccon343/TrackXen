"""A pretend PTZ camera: answers VISCA over IP and sends FreeD, like a P400 would.

Its numbers (speed table, position units) are made up; the real P400's are measured
in the camera lab. Position units here: 16 per degree for pan/tilt, 0-16384 for zoom.
"""

from __future__ import annotations

import asyncio
import bisect
import math
import time
from collections import deque
from dataclasses import dataclass, field

from trackxen.devices import freed, visca

UNITS_PER_DEGREE = 16
ZOOM_MAX = 16384
# Degrees per second for VISCA pan/tilt speeds 1..24 (made up, Sony-like curve)
SPEED_TABLE = [0.4, 0.8, 1.3, 2.0, 2.9, 4.0, 5.4, 7.0, 9.0, 11.5, 14.4, 18.0,
               22.0, 27.0, 33.0, 40.0, 48.0, 57.0, 67.0, 78.0, 90.0, 103.0, 117.0, 130.0]
PAN_LIMIT, TILT_LIMIT = 170.0, 90.0
ZOOM_DIRECT_RATE = 6000.0  # zoom units per second for zoom direct and preset recalls
PRESET_SPEED = 16  # pan/tilt speed step of preset recalls


def speed_to_dps(speed: int) -> float:
    return SPEED_TABLE[max(1, min(len(SPEED_TABLE), speed)) - 1]


@dataclass
class CameraState:
    pan: float = 0.0  # degrees
    tilt: float = 0.0
    zoom: float = 0.0  # 0..ZOOM_MAX
    pan_rate: float = 0.0  # degrees per second
    tilt_rate: float = 0.0
    zoom_rate: float = 0.0  # units per second
    target: tuple[float, float] | None = None  # absolute move target (pan, tilt)
    target_speed: tuple[float, float] = (0.0, 0.0)
    zoom_target: float | None = None  # zoom direct / preset recall target
    pending: list = field(default_factory=list)  # (addr, seq) waiting for absolute-move completion
    presets: dict = field(default_factory=dict)  # number -> (pan, tilt, zoom)


class FakeCamera(asyncio.DatagramProtocol):
    """`command_delay` makes commands take effect late, like a real camera's mechanics."""

    def __init__(self, freed_target: tuple[str, int] | None = None, camera_id: int = 1,
                 freed_rate: float = 50.0, command_delay: float = 0.0):
        self.state = CameraState()
        self.freed_target, self.camera_id, self.freed_rate = freed_target, camera_id, freed_rate
        self.command_delay = command_delay
        self.transport: asyncio.DatagramTransport | None = None
        self.received: list[bytes] = []
        self.history: deque[tuple[float, float, float, float]] = deque(maxlen=4000)  # t, pan, tilt, zoom
        self._tasks: list[asyncio.Task] = []
        self._freed_transport: asyncio.DatagramTransport | None = None

    def pose_at(self, t: float) -> tuple[float, float, float]:
        """Pan, tilt and zoom at monotonic time `t` (interpolated from the recent history)."""
        hist = list(self.history)
        if not hist:
            return self.state.pan, self.state.tilt, self.state.zoom
        times = [h[0] for h in hist]
        i = bisect.bisect_left(times, t)
        if i <= 0:
            return hist[0][1:]
        if i >= len(hist):
            return hist[-1][1:]
        (t0, *a), (t1, *b) = hist[i - 1], hist[i]
        k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return tuple(x + (y - x) * k for x, y in zip(a, b))  # type: ignore[return-value]

    # --- lifecycle --------------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = visca.VISCA_PORT) -> int:
        from trackxen.devices.udp import open_udp

        self.transport, _ = await open_udp(lambda: self, local=(host, port))
        self._tasks.append(asyncio.create_task(self._simulate()))
        if self.freed_target:
            self._freed_transport, _ = await open_udp(asyncio.DatagramProtocol)
            self._tasks.append(asyncio.create_task(self._send_freed()))
        return self.transport.get_extra_info("sockname")[1]

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for tr in (self.transport, self._freed_transport):
            if tr:
                tr.close()

    # --- VISCA ------------------------------------------------------------

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            payload_type, seq, payload = visca.unframe(data)
        except ValueError:
            return
        if payload_type == visca.PT_CONTROL:
            self._reply(addr, visca.PT_CONTROL_REPLY, b"\x01", seq)
            return
        for msg in visca.split_messages(payload):
            self.received.append(msg)
            self._handle(msg, addr, seq)

    def _reply(self, addr, payload_type: int, payload: bytes, seq: int) -> None:
        if self.transport:
            self.transport.sendto(visca.frame(payload_type, payload, seq), addr)

    def _handle(self, msg: bytes, addr, seq: int) -> None:
        s = self.state
        ack = b"\x90\x41\xff"
        if msg[1] == 0x09:  # inquiry
            if msg[2:4] == b"\x06\x12":
                data = (visca.to_nibbles(round(s.pan * UNITS_PER_DEGREE))
                        + visca.to_nibbles(round(s.tilt * UNITS_PER_DEGREE)))
            elif msg[2:4] == b"\x04\x47":
                data = visca.to_nibbles(round(s.zoom))
            elif msg[2:4] == b"\x04\x00":
                data = b"\x02"
            else:
                self._reply(addr, visca.PT_REPLY, b"\x90\x60\x02\xff", seq)
                return
            self._reply(addr, visca.PT_REPLY, b"\x90\x50" + data + b"\xff", seq)
            return

        self._reply(addr, visca.PT_REPLY, ack, seq)
        if self.command_delay > 0:
            asyncio.get_running_loop().call_later(self.command_delay, self._execute, msg, addr, seq)
        else:
            self._execute(msg, addr, seq)

    def _execute(self, msg: bytes, addr, seq: int) -> None:
        s = self.state
        done = b"\x90\x51\xff"
        cmd = msg[1:4]
        if cmd == b"\x01\x06\x01":  # pan/tilt drive
            pan_speed, tilt_speed, pan_dir, tilt_dir = msg[4], msg[5], msg[6], msg[7]
            s.target = None
            s.pan_rate = {1: -1, 2: 1}.get(pan_dir, 0) * speed_to_dps(pan_speed)
            s.tilt_rate = {1: 1, 2: -1}.get(tilt_dir, 0) * speed_to_dps(tilt_speed)
            self._reply(addr, visca.PT_REPLY, done, seq)
        elif cmd == b"\x01\x06\x02":  # absolute position
            pan, tilt = visca.decode_pan_tilt(msg[6:14])
            s.target = (pan / UNITS_PER_DEGREE, tilt / UNITS_PER_DEGREE)
            s.pan_rate = s.tilt_rate = 0.0  # an absolute move replaces a running drive
            s.target_speed = (speed_to_dps(msg[4]), speed_to_dps(msg[5]))
            s.pending.append((addr, seq))
        elif cmd == b"\x01\x04\x07":  # zoom
            p = msg[4]
            s.zoom_target = None
            s.zoom_rate = 0.0 if p == 0 else (800.0 * ((p & 0x0F) + 1)) * (1 if p & 0xF0 == 0x20 else -1)
            self._reply(addr, visca.PT_REPLY, done, seq)
        elif cmd == b"\x01\x04\x47":  # zoom direct
            s.zoom_rate, s.zoom_target = 0.0, float(visca.decode_position(msg[4:8]))
            self._reply(addr, visca.PT_REPLY, done, seq)
        elif cmd == b"\x01\x04\x3f":  # preset: 01 = set, 02 = recall
            if msg[4] == 0x01:
                s.presets[msg[5]] = (s.pan, s.tilt, s.zoom)
            elif msg[4] == 0x02 and msg[5] in s.presets:
                pan, tilt, zoom = s.presets[msg[5]]
                s.target, s.target_speed = (pan, tilt), (speed_to_dps(PRESET_SPEED),) * 2
                s.pan_rate = s.tilt_rate = 0.0
                s.zoom_rate, s.zoom_target = 0.0, zoom
            self._reply(addr, visca.PT_REPLY, done, seq)
        elif cmd == b"\x01\x06\x04":  # home
            s.target, s.target_speed = (0.0, 0.0), (speed_to_dps(20), speed_to_dps(20))
            s.pan_rate = s.tilt_rate = 0.0
            s.pending.append((addr, seq))
        else:
            self._reply(addr, visca.PT_REPLY, done, seq)

    # --- motion and FreeD ---------------------------------------------------

    async def _simulate(self, hz: float = 200.0) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(1 / hz)
            now = time.monotonic()
            self.step(now - last)
            last = now

    def step(self, dt: float) -> None:
        s = self.state
        if s.target is not None:
            for axis, speed in zip(("pan", "tilt"), s.target_speed):
                goal = s.target[0 if axis == "pan" else 1]
                cur = getattr(s, axis)
                delta = goal - cur
                move = math.copysign(min(abs(delta), speed * dt), delta)
                setattr(s, axis, cur + move)
            if abs(s.pan - s.target[0]) < 1e-6 and abs(s.tilt - s.target[1]) < 1e-6:
                s.target = None
                for addr, seq in s.pending:
                    self._reply(addr, visca.PT_REPLY, b"\x90\x51\xff", seq)
                s.pending.clear()
        else:
            s.pan = max(-PAN_LIMIT, min(PAN_LIMIT, s.pan + s.pan_rate * dt))
            s.tilt = max(-TILT_LIMIT, min(TILT_LIMIT, s.tilt + s.tilt_rate * dt))
        if s.zoom_target is not None:
            delta = s.zoom_target - s.zoom
            s.zoom += math.copysign(min(abs(delta), ZOOM_DIRECT_RATE * dt), delta)
            if abs(s.zoom - s.zoom_target) < 1e-6:
                s.zoom_target = None
        s.zoom = max(0.0, min(ZOOM_MAX, s.zoom + s.zoom_rate * dt))
        self.history.append((time.monotonic(), s.pan, s.tilt, s.zoom))

    async def _send_freed(self) -> None:
        while True:
            await asyncio.sleep(1 / self.freed_rate)
            s = self.state
            packet = freed.encode(freed.FreeDPosition(
                self.camera_id, s.pan, s.tilt, 0.0, 0.0, 0.0, 0.0, round(s.zoom), 0, 0))
            if self._freed_transport:
                self._freed_transport.sendto(packet, self.freed_target)

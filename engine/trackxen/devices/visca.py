"""VISCA over IP (Sony-style VISCA in an 8-byte header, UDP port 52381).

Message builders return raw VISCA bytes (0x81 ... 0xFF). `frame()` adds the
VISCA-over-IP header; `ViscaClient` sends messages and waits for the replies.
Command bytes follow the Sony VISCA standard used by BirdDog's published lists;
P400-specific behaviour (speed ranges, position units) is measured in the lab.
"""

from __future__ import annotations

import asyncio
import enum
import struct
from dataclasses import dataclass

VISCA_PORT = 52381

# VISCA-over-IP payload types
PT_COMMAND = 0x0100
PT_INQUIRY = 0x0110
PT_REPLY = 0x0111
PT_DEVICE_SETTING = 0x0120
PT_CONTROL = 0x0200
PT_CONTROL_REPLY = 0x0201

# Sony ranges; the P400's real limits are measured in the camera lab
PAN_SPEED_MAX = 0x18
TILT_SPEED_MAX = 0x17
ZOOM_SPEED_MAX = 7
PRESET_MAX = 127

ERRORS = {
    0x01: "message length error",
    0x02: "syntax error",
    0x03: "command buffer full",
    0x04: "command cancelled",
    0x05: "no socket",
    0x41: "command not executable",
}


class PanDir(enum.IntEnum):
    LEFT = 1
    RIGHT = 2
    STOP = 3


class TiltDir(enum.IntEnum):
    UP = 1
    DOWN = 2
    STOP = 3


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def to_nibbles(value: int, count: int = 4) -> bytes:
    """Encode a value as `count` nibble bytes (0x0N), two's complement for negatives."""
    v = value & ((1 << (4 * count)) - 1)
    return bytes((v >> (4 * (count - 1 - i))) & 0x0F for i in range(count))


def from_nibbles(data: bytes, signed: bool = True) -> int:
    v = 0
    for b in data:
        v = (v << 4) | (b & 0x0F)
    bits = 4 * len(data)
    if signed and v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


# --- Commands -------------------------------------------------------------


def pan_tilt_drive(pan_speed: int, tilt_speed: int, pan: PanDir, tilt: TiltDir) -> bytes:
    return bytes([
        0x81, 0x01, 0x06, 0x01,
        _clamp(pan_speed, 1, PAN_SPEED_MAX),
        _clamp(tilt_speed, 1, TILT_SPEED_MAX),
        pan, tilt, 0xFF,
    ])


def pan_tilt_stop() -> bytes:
    return pan_tilt_drive(1, 1, PanDir.STOP, TiltDir.STOP)


def pan_tilt_absolute(pan: int, tilt: int, pan_speed: int, tilt_speed: int) -> bytes:
    return (
        bytes([0x81, 0x01, 0x06, 0x02,
               _clamp(pan_speed, 1, PAN_SPEED_MAX), _clamp(tilt_speed, 1, TILT_SPEED_MAX)])
        + to_nibbles(pan) + to_nibbles(tilt) + b"\xff"
    )


def pan_tilt_relative(pan: int, tilt: int, pan_speed: int, tilt_speed: int) -> bytes:
    return (
        bytes([0x81, 0x01, 0x06, 0x03,
               _clamp(pan_speed, 1, PAN_SPEED_MAX), _clamp(tilt_speed, 1, TILT_SPEED_MAX)])
        + to_nibbles(pan) + to_nibbles(tilt) + b"\xff"
    )


def pan_tilt_home() -> bytes:
    return bytes([0x81, 0x01, 0x06, 0x04, 0xFF])


def zoom_stop() -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF])


def zoom_tele(speed: int) -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x07, 0x20 | _clamp(speed, 0, ZOOM_SPEED_MAX), 0xFF])


def zoom_wide(speed: int) -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x07, 0x30 | _clamp(speed, 0, ZOOM_SPEED_MAX), 0xFF])


def zoom_direct(position: int) -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x47]) + to_nibbles(_clamp(position, 0, 0xFFFF)) + b"\xff"


def focus_auto() -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x38, 0x02, 0xFF])


def focus_manual() -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x38, 0x03, 0xFF])


def focus_one_push() -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x18, 0x01, 0xFF])


def preset_set(number: int) -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x3F, 0x01, _clamp(number, 0, PRESET_MAX), 0xFF])


def preset_recall(number: int) -> bytes:
    return bytes([0x81, 0x01, 0x04, 0x3F, 0x02, _clamp(number, 0, PRESET_MAX), 0xFF])


# --- Inquiries ------------------------------------------------------------


def inq_power() -> bytes:
    return bytes([0x81, 0x09, 0x04, 0x00, 0xFF])


def inq_pan_tilt_position() -> bytes:
    return bytes([0x81, 0x09, 0x06, 0x12, 0xFF])


def inq_zoom_position() -> bytes:
    return bytes([0x81, 0x09, 0x04, 0x47, 0xFF])


def inq_focus_position() -> bytes:
    return bytes([0x81, 0x09, 0x04, 0x48, 0xFF])


def decode_pan_tilt(data: bytes) -> tuple[int, int]:
    """Pan and tilt from a pan/tilt position reply (8 nibbles, signed)."""
    if len(data) < 8:
        raise ValueError(f"pan/tilt reply too short: {data.hex()}")
    return from_nibbles(data[0:4]), from_nibbles(data[4:8])


def decode_position(data: bytes) -> int:
    """Zoom or focus position from a 4-nibble reply (unsigned)."""
    if len(data) < 4:
        raise ValueError(f"position reply too short: {data.hex()}")
    return from_nibbles(data[0:4], signed=False)


# --- Replies --------------------------------------------------------------


class ReplyKind(enum.Enum):
    ACK = "ack"
    COMPLETION = "completion"
    ERROR = "error"


@dataclass(frozen=True)
class Reply:
    kind: ReplyKind
    socket: int
    data: bytes = b""
    error: int | None = None

    @property
    def error_text(self) -> str:
        return ERRORS.get(self.error or 0, f"error 0x{self.error or 0:02x}")


def parse_reply(msg: bytes) -> Reply:
    if len(msg) < 3 or msg[-1] != 0xFF or msg[0] & 0xF0 != 0x90:
        raise ValueError(f"not a VISCA reply: {msg.hex()}")
    kind, socket = msg[1] & 0xF0, msg[1] & 0x0F
    if kind == 0x40:
        return Reply(ReplyKind.ACK, socket)
    if kind == 0x50:
        return Reply(ReplyKind.COMPLETION, socket, data=msg[2:-1])
    if kind == 0x60:
        return Reply(ReplyKind.ERROR, socket, error=msg[2] if len(msg) > 3 else None)
    raise ValueError(f"unknown VISCA reply: {msg.hex()}")


def split_messages(payload: bytes) -> list[bytes]:
    """Split a payload into VISCA messages (each ends with 0xFF)."""
    out, start = [], 0
    for i, b in enumerate(payload):
        if b == 0xFF:
            out.append(payload[start:i + 1])
            start = i + 1
    return out


# --- VISCA-over-IP framing -------------------------------------------------


def frame(payload_type: int, payload: bytes, seq: int) -> bytes:
    return struct.pack(">HHI", payload_type, len(payload), seq & 0xFFFFFFFF) + payload


def unframe(packet: bytes) -> tuple[int, int, bytes]:
    """Payload type, sequence number and payload of a VISCA-over-IP packet."""
    if len(packet) < 8:
        raise ValueError(f"packet too short: {packet.hex()}")
    payload_type, length, seq = struct.unpack(">HHI", packet[:8])
    return payload_type, seq, packet[8:8 + length]


# --- Client ---------------------------------------------------------------


class ViscaError(Exception):
    pass


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client: ViscaClient):
        self.client = client

    def datagram_received(self, data: bytes, addr) -> None:
        if self.client._remote and addr[0] == self.client._remote[0]:  # only the camera's replies
            self.client._on_packet(data)

    def error_received(self, exc: Exception) -> None:
        self.client._on_error(exc)


class ViscaClient:
    """Async VISCA-over-IP client that keeps one request in flight at a time.

    `use_header=False` sends bare VISCA, for cameras that don't use the IP header.
    """

    def __init__(self, host: str, port: int = VISCA_PORT, timeout: float = 1.0,
                 use_header: bool = True):
        self.host, self.port, self.timeout, self.use_header = host, port, timeout, use_header
        self._transport: asyncio.DatagramTransport | None = None
        self._remote: tuple[str, int] | None = None
        self._replies: asyncio.Queue[Reply] = asyncio.Queue()
        self._control = asyncio.Event()
        self._lock = asyncio.Lock()
        self._seq = 0
        self.last_error: Exception | None = None

    async def connect(self) -> None:
        from trackxen.devices.udp import open_udp, resolve

        self._remote = (resolve(self.host), self.port)
        self._transport, _ = await open_udp(lambda: _Protocol(self))
        if self.use_header:
            await self.reset_sequence()

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    async def __aenter__(self) -> ViscaClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()

    async def reset_sequence(self) -> bool:
        """Reset the camera's sequence counter. Returns False if it didn't answer."""
        self._seq = 0
        self._control.clear()
        self._send(PT_CONTROL, b"\x01")
        try:
            await asyncio.wait_for(self._control.wait(), self.timeout)
            return True
        except TimeoutError:
            return False

    async def command(self, msg: bytes, wait_completion: bool = False) -> Reply:
        """Send a command and wait for its ACK (and completion if asked)."""
        async with self._lock:
            self._drain()
            self._send(PT_COMMAND, msg)
            reply = await self._next_reply()
            if reply.kind is ReplyKind.ACK and wait_completion:
                reply = await self._next_reply(timeout=max(self.timeout, 30.0))
            if reply.kind is ReplyKind.ERROR:
                raise ViscaError(f"{msg.hex(' ')}: {reply.error_text}")
            return reply

    async def inquiry(self, msg: bytes) -> bytes:
        async with self._lock:
            self._drain()
            self._send(PT_INQUIRY, msg)
            reply = await self._next_reply()
            if reply.kind is ReplyKind.ERROR:
                raise ViscaError(f"{msg.hex(' ')}: {reply.error_text}")
            return reply.data

    async def pan_tilt_position(self) -> tuple[int, int]:
        return decode_pan_tilt(await self.inquiry(inq_pan_tilt_position()))

    async def zoom_position(self) -> int:
        return decode_position(await self.inquiry(inq_zoom_position()))

    async def stop_all(self) -> None:
        """Stop pan, tilt and zoom. Used by every tool on exit."""
        await self.command(pan_tilt_stop())
        await self.command(zoom_stop())

    def _send(self, payload_type: int, msg: bytes) -> None:
        if not self._transport:
            raise ViscaError("not connected")
        if self.use_header:
            self._seq += 1
            self._transport.sendto(frame(payload_type, msg, self._seq), self._remote)
        else:
            self._transport.sendto(msg, self._remote)

    async def _next_reply(self, timeout: float | None = None) -> Reply:
        try:
            return await asyncio.wait_for(self._replies.get(), timeout or self.timeout)
        except TimeoutError:
            raise ViscaError(f"no reply from {self.host}:{self.port}") from None

    def _drain(self) -> None:
        while not self._replies.empty():
            self._replies.get_nowait()

    def _on_packet(self, data: bytes) -> None:
        payload = data
        if self.use_header:
            try:
                payload_type, _, payload = unframe(data)
            except ValueError:
                return
            if payload_type == PT_CONTROL_REPLY:
                self._control.set()
                return
        for msg in split_messages(payload):
            try:
                self._replies.put_nowait(parse_reply(msg))
            except ValueError:
                pass

    def _on_error(self, exc: Exception) -> None:
        self.last_error = exc

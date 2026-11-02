"""Midas MR18 / Behringer X Air mixers over OSC (UDP 10024).

Same method as Bitfocus Companion's X Air module: every 9.5 s renew `/xremote`
(parameter changes) and the `/meters/1` subscription. `/meters/1` carries 40 levels:
a 4-byte little-endian count, then little-endian int16 values in 1/256 dB. The first
16 are the input channels.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from trackxen.devices.udp import open_udp, resolve_async

XAIR_PORT = 10024
RENEW_INTERVAL = 9.5
INFO_INTERVAL = 30  # seconds: MixerLink asks for channel names, mutes and faders again (and checks for levels)
METERS1_COUNT = 40
INPUT_CHANNELS = 16

METERS1_LABELS = (
    [f"Ch {i}" for i in range(1, 17)]
    + ["Aux L", "Aux R"]
    + [f"Rtn {i} {side}" for i in range(1, 5) for side in ("L", "R")]
    + [f"Bus {i}" for i in range(1, 7)]
    + [f"FX Send {i}" for i in range(1, 5)]
    + ["Main L", "Main R", "Mon L", "Mon R"]
)


def osc_message(address: str, *args) -> bytes:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def parse_osc(data: bytes) -> tuple[str, list]:
    msg = OscMessage(data)
    return msg.address, list(msg.params)


def parse_meters1(blob: bytes) -> list[float]:
    """dB levels from a /meters/1 blob."""
    if len(blob) < 4:
        raise ValueError("meter blob too short")
    count = int.from_bytes(blob[:4], "little")
    if count != METERS1_COUNT or len(blob) < 4 + 2 * count:
        raise ValueError(f"unexpected meter blob (count {count}, {len(blob)} bytes)")
    return [v / 256.0 for v in struct.unpack_from(f"<{count}h", blob, 4)]


def channel_address(channel: int, leaf: str) -> str:
    """e.g. channel_address(3, "config/name") -> "/ch/03/config/name"."""
    return f"/ch/{channel:02d}/{leaf}"


@dataclass(frozen=True)
class MixerInfo:
    ip: str
    name: str
    model: str
    firmware: str


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_message: Callable[[str, list, tuple[str, int]], None]):
        self.on_message = on_message

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            address, args = parse_osc(data)
        except Exception:  # malformed or bundle: ignore
            return
        self.on_message(address, args, addr)


async def discover(timeout: float = 1.5, broadcast: str = "255.255.255.255") -> list[MixerInfo]:
    """Find MR18 / X Air mixers on the local network with an /xinfo broadcast."""
    found: dict[str, MixerInfo] = {}

    def on_message(address: str, args: list, addr) -> None:
        if address == "/xinfo" and len(args) >= 4:
            found[str(args[0])] = MixerInfo(*(str(a) for a in args[:4]))

    transport, _ = await open_udp(lambda: _Protocol(on_message), broadcast=True)
    try:
        transport.sendto(osc_message("/xinfo"), (broadcast, XAIR_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(found.values())


class XAirClient:
    """Connection to one mixer: live input levels, channel names, mutes and faders.

    `on_meters(levels_db)` gets all 40 levels; `on_value(address, args)` gets every
    other message (names, mutes, fader moves pushed through /xremote).
    """

    def __init__(self, host: str, port: int = XAIR_PORT,
                 on_meters: Callable[[list[float]], None] | None = None,
                 on_value: Callable[[str, list], None] | None = None,
                 send_update_rate: bool = False):
        self.host, self.port = host, port
        self.on_meters, self.on_value = on_meters, on_value
        # Companion also sends "/-local/updrate 1" each pulse; off unless the lab shows it's needed
        self.send_update_rate = send_update_rate
        self._transport: asyncio.DatagramTransport | None = None
        self._remote: tuple[str, int] | None = None
        self._pulse_task: asyncio.Task | None = None

    async def connect(self) -> None:
        # One socket for sending and receiving: the mixer replies to the sender's port
        self._remote = (await resolve_async(self.host), self.port)
        self._transport, _ = await open_udp(lambda: _Protocol(self._on_message))
        self.pulse()
        self._pulse_task = asyncio.create_task(self._pulse_loop())

    def close(self) -> None:
        if self._pulse_task:
            self._pulse_task.cancel()
        if self._transport:
            self._transport.close()
            self._transport = None

    def send(self, address: str, *args) -> None:
        if self._transport:
            self._transport.sendto(osc_message(address, *args), self._remote)

    def pulse(self) -> None:
        self.send("/xremote")
        if self.send_update_rate:
            self.send("/-local/updrate", 1)
        self.send("/meters", "/meters/1", 0)

    def request_channel_info(self, channels: int = INPUT_CHANNELS) -> None:
        """Ask for each input's name, mute (mix/on) and fader; answers arrive via on_value."""
        for ch in range(1, channels + 1):
            for leaf in ("config/name", "mix/on", "mix/fader"):
                self.send(channel_address(ch, leaf))

    async def _pulse_loop(self) -> None:
        while True:
            await asyncio.sleep(RENEW_INTERVAL)
            self.pulse()

    def _on_message(self, address: str, args: list, addr) -> None:
        if self._remote and addr[0] != self._remote[0]:  # only the mixer's messages
            return
        if address == "/meters/1":
            if self.on_meters and args and isinstance(args[0], (bytes, bytearray)):
                try:
                    self.on_meters(parse_meters1(bytes(args[0])))
                except ValueError:
                    pass
        elif self.on_value:
            self.on_value(address, args)


class MixerLink:
    """An MR18 / X Air for audio-follow: input levels (about 20 a second), the mixer's channel
    names, mutes and faders (asked for again every 30 s; changes arrive through /xremote).
    UDP has no connection: "connected" means levels arrived in the last 2 seconds. Still no levels
    at the next 30 s mark: it starts again with a new socket."""

    def __init__(self, host: str, port: int = XAIR_PORT, on_levels: Callable[[list[float]], None] | None = None,
                 log=None):
        self.host, self.port, self.on_levels, self.log = host, port, on_levels, log
        self.levels = [-90.0] * METERS1_COUNT
        self.names = [""] * INPUT_CHANNELS
        self.muted = [False] * INPUT_CHANNELS
        self.faders = [0.75] * INPUT_CHANNELS
        self.last_levels = 0.0
        self.error = ""
        self._client: XAirClient | None = None
        self._task: asyncio.Task | None = None
        self._was_connected = False

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_levels < 2.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._client:
            self._client.close()

    async def _run(self) -> None:
        while True:
            while self._client is None:
                try:
                    self._client = XAirClient(self.host, self.port, on_meters=self._meters, on_value=self._value)
                    await self._client.connect()
                    self.error = ""
                except (OSError, ValueError) as e:  # e.g. the name doesn't resolve yet (ValueError: "192.168.1..60")
                    self._client.close()
                    self._client, self.error = None, str(e) or type(e).__name__
                    await asyncio.sleep(5.0)
            self._client.request_channel_info()
            for _ in range(INFO_INTERVAL):
                await asyncio.sleep(1.0)
                if self.connected != self._was_connected:
                    self._was_connected = self.connected
                    if self.log:
                        self.log.event("mixer", f"mixer {self.host}: " + ("levels arriving" if self.connected else "no levels"),
                                       level="info" if self.connected else "warning")
            if not self.connected:  # (on Windows one receive error ends a UDP socket's reading for good)
                self._client.close()
                self._client = None

    def _meters(self, levels: list[float]) -> None:
        self.levels, self.last_levels = levels, time.monotonic()
        if self.on_levels:
            self.on_levels(levels)

    def _value(self, address: str, args: list) -> None:
        parts = address.split("/")  # "", "ch", "01", "config"|"mix", leaf
        if len(parts) != 5 or parts[1] != "ch" or not args:
            return
        try:
            i = int(parts[2]) - 1
        except ValueError:
            return
        if not 0 <= i < INPUT_CHANNELS:
            return
        leaf = parts[3] + "/" + parts[4]
        if leaf == "config/name":
            self.names[i] = str(args[0])
        elif leaf == "mix/on":
            self.muted[i] = not bool(args[0])
        elif leaf == "mix/fader":
            self.faders[i] = float(args[0])

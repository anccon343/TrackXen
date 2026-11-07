"""vMix TCP API (port 8099): live tally and the input list.

Tally lines look like "TALLY OK 0121": one digit per input, in input order,
0 = off, 1 = program, 2 = preview. "XML" returns "XML <bytes>" followed by the
vMix state document.
"""

from __future__ import annotations

import asyncio
import enum
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

VMIX_PORT = 8099
REREAD_INTERVAL = 10.0  # seconds between input list reads
RECONNECT_DELAY = 3.0
STALE_GRACE = 20.0  # seconds the last tally is kept after the link drops, so a blip doesn't take the live camera off air


class Tally(enum.IntEnum):
    OFF = 0
    PROGRAM = 1
    PREVIEW = 2


def parse_tally(line: str) -> list[Tally] | None:
    """Tally per input (index 0 = input 1), or None if the line isn't a tally line."""
    parts = line.strip().split()
    if len(parts) < 3 or parts[0] != "TALLY" or parts[1] != "OK":
        return None
    return [Tally(int(c)) if c in "012" else Tally.OFF for c in parts[2]]


@dataclass(frozen=True)
class VmixInput:
    number: int
    key: str
    title: str
    type: str
    short_title: str


def parse_inputs(xml_text: str) -> list[VmixInput]:
    root = ET.fromstring(xml_text)
    inputs = []
    for el in root.iter("input"):
        if "number" not in el.attrib:  # overlay/transition elements share the tag name elsewhere
            continue
        inputs.append(VmixInput(
            number=int(el.attrib["number"]),
            key=el.attrib.get("key", ""),
            title=el.attrib.get("title", ""),
            type=el.attrib.get("type", ""),
            short_title=el.attrib.get("shortTitle", ""),
        ))
    return inputs


class VmixClient:
    """One TCP connection to vMix's API."""

    def __init__(self, host: str, port: int = VMIX_PORT, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)

    def close(self) -> None:
        if self._writer:
            self._writer.close()
            self._writer = None

    async def send(self, command: str) -> None:
        assert self._writer is not None, "not connected"
        self._writer.write((command + "\r\n").encode())
        await self._writer.drain()

    async def read_line(self) -> str:
        assert self._reader is not None, "not connected"
        line = await self._reader.readline()
        if not line:
            raise ConnectionError("vMix closed the connection")
        return line.decode("utf-8", "replace").rstrip("\r\n")

    async def request_xml(self) -> str:
        """Send XML and return the document (use a connection without subscriptions)."""
        await self.send("XML")
        while True:
            header = await asyncio.wait_for(self.read_line(), self.timeout)
            if header.startswith("XML "):
                break
        length = int(header.split()[1])
        assert self._reader is not None
        data = await asyncio.wait_for(self._reader.readexactly(length), self.timeout)
        return data.decode("utf-8", "replace")

    async def tally_events(self):
        """Subscribe to tally and yield the tally list on every change."""
        await self.send("SUBSCRIBE TALLY")
        await self.send("TALLY")
        while True:
            tally = parse_tally(await self.read_line())
            if tally is not None:
                yield tally


class VmixLink:
    """Stays connected to vMix: the input list (re-read every 10 s, which also notices a dead
    connection) and live tally on a second connection. Reconnects by itself every 3 s.

    When the link drops, the last tally is kept for STALE_GRACE seconds (`connected` is False at once):
    treating the live camera as off Program on a short outage lifted its on-air limits and let
    audio-follow send it to a preset while it was still on air."""

    def __init__(self, host: str, port: int = VMIX_PORT, on_change: Callable[[], None] | None = None, log=None):
        self.host, self.port, self.on_change, self.log = host, port, on_change, log
        self.inputs: list[VmixInput] = []
        self.tally: list[Tally] = []
        self.connected = False
        self.error = ""
        self._task: asyncio.Task | None = None
        self._lost_at = 0.0  # when the link last dropped

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def state_of(self, input_ref: str) -> Tally | None:
        """Tally of an input given by its key (or its number)."""
        number = next((i.number for i in self.inputs if input_ref and input_ref in (i.key, str(i.number))), None)
        if number is None or number > len(self.tally):
            return None
        return self.tally[number - 1]

    def suggest(self, ndi_name: str) -> VmixInput | None:
        """The input showing this NDI source (vMix names NDI inputs after the source)."""
        name = ndi_name.lower()
        return next((i for i in self.inputs if name and (name in i.title.lower() or i.title.lower() in name)), None)

    async def _run(self) -> None:
        while True:
            try:
                await self._session()
            except Exception as e:  # noqa: BLE001 - whatever ended it (also e.g. asyncio.IncompleteReadError), reconnect
                self.error = str(e) or type(e).__name__
            if self.connected:
                self._lost_at = time.monotonic()
                if self.log:
                    self.log.event("vmix", f"vMix connection lost: {self.error}", level="warning")
            self.connected = False
            if self.tally and time.monotonic() - self._lost_at >= STALE_GRACE:
                self.tally = []
                self._changed()
            await asyncio.sleep(RECONNECT_DELAY)

    def _changed(self) -> None:
        """Tell the owner the tally (or the input numbering) changed; its errors must not end the link."""
        if not self.on_change:
            return
        try:
            self.on_change()
        except Exception as e:  # noqa: BLE001 - keep following vMix
            if self.log:
                self.log.event("vmix", f"error handling a vMix tally change: {e!r}", level="error")

    async def _session(self) -> None:
        tally_conn, xml_conn = VmixClient(self.host, self.port), VmixClient(self.host, self.port)
        try:
            await tally_conn.connect()
            await xml_conn.connect()
            self.inputs = parse_inputs(await xml_conn.request_xml())
            self.connected, self.error = True, ""
            if self.log:
                self.log.event("vmix", f"connected to vMix at {self.host} ({len(self.inputs)} inputs)")
            tasks = [asyncio.create_task(self._follow_tally(tally_conn)), asyncio.create_task(self._reread_inputs(xml_conn))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()  # raises what went wrong
                raise ConnectionError("vMix ended the connection")
            finally:
                for task in tasks:
                    task.cancel()
        finally:
            tally_conn.close()
            xml_conn.close()

    async def _follow_tally(self, conn: VmixClient) -> None:
        first = True  # (the first one of a connection: the inputs may have been renumbered while we were away)
        async for tally in conn.tally_events():
            if tally != self.tally or first:
                self.tally, first = tally, False
                self._changed()

    async def _reread_inputs(self, conn: VmixClient) -> None:
        try:
            while True:
                await asyncio.sleep(REREAD_INTERVAL)
                try:
                    inputs = parse_inputs(await conn.request_xml())
                except Exception:  # noqa: BLE001 - slow or garbled: once more on a new connection, so the tally
                    conn.close()  # connection isn't dropped for it; if that fails too, vMix is gone
                    conn = VmixClient(self.host, self.port)
                    await conn.connect()
                    inputs = parse_inputs(await conn.request_xml())
                if inputs != self.inputs:  # inputs added, removed or moved: the tally digits mean other inputs now
                    self.inputs = inputs
                    self._changed()
        finally:
            conn.close()

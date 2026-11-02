"""A pretend vMix TCP API: answers XML and TALLY, and cycles the tally every few seconds."""

from __future__ import annotations

import asyncio
from xml.sax.saxutils import escape, quoteattr

INPUTS = [
    (1, "NDI", "BIRDDOG-CAM1 (CAM)"),
    (2, "NDI", "BIRDDOG-CAM2 (CAM)"),
    (3, "NDI", "BIRDDOG-CAM3 (CAM)"),
    (4, "NDI", "BIRDDOG-CAM4 (CAM)"),
    (5, "Colour", "Black"),
]


def state_xml(inputs_list=INPUTS) -> str:
    """The vMix state document; an input is (number, type, title) or (number, type, title, key)."""
    inputs = "".join(
        f'<input key={quoteattr(key[0] if key else f"key-{n}")} number="{n}" type="{t}" title={quoteattr(title)} '
        f'shortTitle={quoteattr(title)} state="Running">{escape(title)}</input>'
        for n, t, title, *key in inputs_list)
    return f"<vmix><version>28.0.0.0</version><inputs>{inputs}</inputs></vmix>"


class FakeVmix:
    """`first_title` renames input 1 (e.g. to the simulator's NDI source, so TrackXen can match it).
    `stop()` hangs up on every client too, like vMix quitting; `stall_xml` leaves that many XML
    requests unanswered, like a vMix too busy to answer in time."""

    def __init__(self, cycle_seconds: float = 3.0, first_title: str = ""):
        self.cycle_seconds = cycle_seconds
        self.inputs = list(INPUTS)
        if first_title:
            self.inputs[0] = (1, "NDI", first_title)
        self.program, self.preview = 1, 2
        self.stall_xml = 0
        self.xml_requests = 0
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._task: asyncio.Task | None = None

    def tally_line(self) -> str:
        digits = ["0"] * len(self.inputs)
        digits[self.preview - 1] = "2"
        digits[self.program - 1] = "1"
        return "TALLY OK " + "".join(digits) + "\r\n"

    async def start(self, host: str = "127.0.0.1", port: int = 8099) -> int:
        self._server = await asyncio.start_server(self._client, host, port)
        self._task = asyncio.create_task(self._cycle())
        return self._server.sockets[0].getsockname()[1]

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._server:
            self._server.close()  # (stops listening only: the clients' connections stay open)
        for writer in list(self._clients):
            writer.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            while line := await reader.readline():
                cmd = line.decode().strip().upper()
                if cmd == "XML":
                    self.xml_requests += 1
                    if self.stall_xml > 0:
                        self.stall_xml -= 1
                        continue
                    body = state_xml(self.inputs).encode()
                    writer.write(f"XML {len(body)}\r\n".encode() + body)
                elif cmd == "TALLY":
                    writer.write(self.tally_line().encode())
                elif cmd == "SUBSCRIBE TALLY":
                    self._subscribers.add(writer)
                    writer.write(b"SUBSCRIBE OK TALLY\r\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self._clients.discard(writer)
            self._subscribers.discard(writer)
            writer.close()

    async def push_tally(self) -> None:
        """Send the tally to the subscribers, as vMix does on every change."""
        for w in list(self._subscribers):
            try:
                w.write(self.tally_line().encode())
                await w.drain()
            except ConnectionError:
                self._subscribers.discard(w)

    async def _cycle(self) -> None:
        cams = 4
        while True:
            await asyncio.sleep(self.cycle_seconds)
            self.program, self.preview = self.preview, self.preview % cams + 1
            await self.push_tally()

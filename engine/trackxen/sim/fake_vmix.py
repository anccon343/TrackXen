"""A pretend vMix TCP API: answers XML and TALLY, and cycles the tally every few seconds."""

from __future__ import annotations

import asyncio

INPUTS = [
    (1, "NDI", "BIRDDOG-CAM1 (CAM)"),
    (2, "NDI", "BIRDDOG-CAM2 (CAM)"),
    (3, "NDI", "BIRDDOG-CAM3 (CAM)"),
    (4, "NDI", "BIRDDOG-CAM4 (CAM)"),
    (5, "Colour", "Black"),
]


def state_xml(inputs_list=INPUTS) -> str:
    inputs = "".join(
        f'<input key="key-{n}" number="{n}" type="{t}" title="{title}" shortTitle="{title}" '
        f'state="Running">{title}</input>'
        for n, t, title in inputs_list)
    return f"<vmix><version>28.0.0.0</version><inputs>{inputs}</inputs></vmix>"


class FakeVmix:
    """`first_title` renames input 1 (e.g. to the simulator's NDI source, so TrackXen can match it)."""

    def __init__(self, cycle_seconds: float = 3.0, first_title: str = ""):
        self.cycle_seconds = cycle_seconds
        self.inputs = list(INPUTS)
        if first_title:
            self.inputs[0] = (1, "NDI", first_title)
        self.program, self.preview = 1, 2
        self._server: asyncio.base_events.Server | None = None
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
            self._server.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                cmd = line.decode().strip().upper()
                if cmd == "XML":
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
            self._subscribers.discard(writer)
            writer.close()

    async def _cycle(self) -> None:
        cams = 4
        while True:
            await asyncio.sleep(self.cycle_seconds)
            self.program, self.preview = self.preview, self.preview % cams + 1
            for w in list(self._subscribers):
                try:
                    w.write(self.tally_line().encode())
                    await w.drain()
                except ConnectionError:
                    self._subscribers.discard(w)

import asyncio

from trackxen.integrations import vmix
from trackxen.sim.fake_vmix import FakeVmix, state_xml


def test_parse_tally():
    assert vmix.parse_tally("TALLY OK 0120") == [
        vmix.Tally.OFF, vmix.Tally.PROGRAM, vmix.Tally.PREVIEW, vmix.Tally.OFF]
    assert vmix.parse_tally("SUBSCRIBE OK TALLY") is None
    assert vmix.parse_tally("TALLY ER") is None


def test_parse_inputs():
    inputs = vmix.parse_inputs(state_xml())
    assert [i.number for i in inputs] == [1, 2, 3, 4, 5]
    assert inputs[0].title == "BIRDDOG-CAM1 (CAM)" and inputs[0].type == "NDI"


def test_client_against_fake_vmix():
    async def run():
        fake = FakeVmix(cycle_seconds=0.2)
        port = await fake.start(port=0)
        try:
            query = vmix.VmixClient("127.0.0.1", port)
            await query.connect()
            inputs = vmix.parse_inputs(await query.request_xml())
            query.close()

            events = vmix.VmixClient("127.0.0.1", port)
            await events.connect()
            seen = []
            async for tally in events.tally_events():
                seen.append(tally)
                if len(seen) == 3:
                    break
            events.close()
        finally:
            fake.stop()
        assert len(inputs) == 5
        assert seen[0][0] is vmix.Tally.PROGRAM and seen[0][1] is vmix.Tally.PREVIEW
        assert seen[-1] != seen[0]

    asyncio.run(run())


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, text, level="info", **_):
        self.events.append((level, text))


async def wait_for(condition, seconds: float) -> bool:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


def test_titles_are_escaped_in_the_fake_xml():
    inputs = vmix.parse_inputs(state_xml([(1, "NDI", 'A & B <cam> "x"')]))
    assert inputs[0].title == 'A & B <cam> "x"'


def test_a_failing_tally_handler_does_not_end_the_link():
    """An exception in on_change (LiveInputs.on_tally) ended the link for good, with connected=True and the tally frozen."""
    async def run():
        fake = FakeVmix(cycle_seconds=0.1)
        port = await fake.start(port=0)
        seen, log = [], Log()

        def on_change():
            seen.append(tuple(link.tally))
            raise KeyError("cam9")

        link = vmix.VmixLink("127.0.0.1", port, on_change=on_change, log=log)
        link.start()
        try:
            assert await wait_for(lambda: len(set(seen)) >= 3, 3.0)  # still following the tally
            assert link.connected and not link._task.done()
            assert any(level == "error" and "cam9" in text for level, text in log.events)
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())


def test_a_cut_off_xml_answer_does_not_end_the_link():
    """vMix closing the connection in the middle of the XML document raised IncompleteReadError (an EOFError),
    which the link didn't catch: it never reconnected."""
    async def run():
        async def cut_off(reader, writer):
            await reader.readline()
            writer.write(b"XML 731\r\n" + b"<vmix>" + b"x" * 14)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(cut_off, "127.0.0.1", 0)
        link = vmix.VmixLink("127.0.0.1", server.sockets[0].getsockname()[1])
        link.start()
        try:
            assert await wait_for(lambda: link.error, 2.0)
            assert "expected" in link.error and not link._task.done() and not link.connected
        finally:
            link.stop()
            server.close()

    asyncio.run(run())


def test_a_slow_input_list_answer_keeps_the_tally(monkeypatch):
    """The 10 s input list read timing out (3 s) ended the tally connection too: the live camera went off Program."""
    monkeypatch.setattr(vmix, "REREAD_INTERVAL", 0.2)

    async def run():
        fake = FakeVmix(cycle_seconds=100)
        port = await fake.start(port=0)
        changes = []
        link = vmix.VmixLink("127.0.0.1", port, on_change=lambda: changes.append(list(link.tally)))
        link.start()
        try:
            assert await wait_for(lambda: link.state_of("key-1") is vmix.Tally.PROGRAM, 2.0)
            fake.stall_xml = 1  # the next input list read gets no answer
            dropped = []
            for _ in range(int(4.0 / 0.05)):
                await asyncio.sleep(0.05)
                if not link.connected or link.state_of("key-1") is not vmix.Tally.PROGRAM:
                    dropped.append(link.error)
            assert not dropped
            assert fake.stall_xml == 0 and fake.xml_requests >= 4  # it asked again on a new connection
            assert all(c and c[0] is vmix.Tally.PROGRAM for c in changes)
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())


def test_vmix_going_away_keeps_the_last_tally_for_a_while(monkeypatch):
    monkeypatch.setattr(vmix, "RECONNECT_DELAY", 0.1)
    monkeypatch.setattr(vmix, "STALE_GRACE", 1.0)

    async def run():
        fake = FakeVmix(cycle_seconds=100)
        port = await fake.start(port=0)
        changes, log = [], Log()
        link = vmix.VmixLink("127.0.0.1", port, on_change=lambda: changes.append(list(link.tally)), log=log)
        link.start()
        try:
            assert await wait_for(lambda: link.state_of("key-1") is vmix.Tally.PROGRAM, 2.0)
            before = len(changes)
            fake.stop()  # vMix quits
            assert await wait_for(lambda: not link.connected, 1.0)
            assert any("connection lost" in text for _, text in log.events)
            await asyncio.sleep(0.3)
            assert link.state_of("key-1") is vmix.Tally.PROGRAM and len(changes) == before  # a blip: still on air
            assert await wait_for(lambda: not link.tally, 2.0)  # gone for longer: the tally is unknown now
            assert changes[-1] == [] and not link._task.done()
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())


def test_renumbered_inputs_are_evaluated_again_after_the_input_list_read(monkeypatch):
    """Input 1 removed in vMix and camera 3 cut to Program: vMix's tally line uses the new numbering at once, the
    input list only catches up at the next read. Then the camera's tally must be worked out again."""
    monkeypatch.setattr(vmix, "REREAD_INTERVAL", 0.3)

    async def run():
        fake = FakeVmix(cycle_seconds=100)
        fake.inputs = [(n, t, title, f"key-{n}") for n, t, title in fake.inputs]  # keys stay with their input
        port = await fake.start(port=0)
        states = []
        link = vmix.VmixLink("127.0.0.1", port, on_change=lambda: states.append(link.state_of("key-3")))
        link.start()
        try:
            assert await wait_for(lambda: link.state_of("key-3") is vmix.Tally.OFF, 2.0)
            fake.inputs = [(n, t, title, key) for n, (_, t, title, key) in enumerate(fake.inputs[1:], start=1)]
            fake.program, fake.preview = 2, 1  # camera 3 (now input 2) on Program
            await fake.push_tally()
            assert await wait_for(lambda: len(link.inputs) == 4, 2.0)
            await asyncio.sleep(0.1)
            assert states[-1] is vmix.Tally.PROGRAM and link.state_of("key-3") is vmix.Tally.PROGRAM
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())

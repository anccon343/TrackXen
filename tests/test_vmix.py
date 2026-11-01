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

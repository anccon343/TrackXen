import asyncio

import pytest

from trackxen.integrations import xair
from trackxen.sim.fake_mixer import FakeMixer, meters1_blob


def test_meter_blob_parsing():
    levels = [-90.0] * 40
    levels[0], levels[15], levels[39] = -18.5, 0.0, -6.25
    parsed = xair.parse_meters1(meters1_blob(levels))
    assert len(parsed) == 40
    assert parsed[0] == pytest.approx(-18.5, abs=1 / 256)
    assert parsed[15] == 0.0 and parsed[39] == pytest.approx(-6.25)
    with pytest.raises(ValueError):
        xair.parse_meters1(meters1_blob([0.0] * 10))
    with pytest.raises(ValueError):
        xair.parse_meters1(b"\x28\x00")


def test_labels_and_addresses():
    assert len(xair.METERS1_LABELS) == xair.METERS1_COUNT
    assert xair.METERS1_LABELS[0] == "Ch 1" and xair.METERS1_LABELS[15] == "Ch 16"
    assert xair.METERS1_LABELS[-1] == "Mon R"
    assert xair.channel_address(3, "config/name") == "/ch/03/config/name"


def test_osc_roundtrip_with_blob():
    data = xair.osc_message("/meters", "/meters/1", 0)
    assert xair.parse_osc(data) == ("/meters", ["/meters/1", 0])
    blob = meters1_blob([-20.0] * 40)
    address, args = xair.parse_osc(xair.osc_message("/meters/1", blob))
    assert address == "/meters/1" and bytes(args[0]) == blob


def test_client_against_fake_mixer():
    async def run():
        mixer = FakeMixer()
        port = await mixer.start(port=0)
        meters, values = [], {}
        client = xair.XAirClient("127.0.0.1", port, on_meters=meters.append,
                                 on_value=lambda a, args: values.__setitem__(a, args))
        await client.connect()
        client.request_channel_info(channels=2)
        try:
            await asyncio.sleep(0.4)
        finally:
            client.close()
            mixer.stop()
        assert "/xremote" in mixer.received and "/meters" in mixer.received
        assert meters and len(meters[-1]) == 40
        assert values["/ch/01/config/name"] == ["Mic 1"]
        assert values["/ch/02/mix/on"] == [1]

    asyncio.run(run())

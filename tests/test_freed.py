import asyncio

import pytest

from trackxen.devices import freed
from trackxen.sim.fake_camera import FakeCamera


def sample(**changes) -> freed.FreeDPosition:
    values = dict(camera_id=3, pan=-12.5, tilt=4.25, roll=0.0, x=100.0, y=-50.0, z=1500.0,
                  zoom=12345, focus=678, spare=0)
    values.update(changes)
    return freed.FreeDPosition(**values)


def test_roundtrip():
    packet = freed.encode(sample())
    assert len(packet) == freed.PACKET_SIZE and packet[0] == freed.D1
    assert freed.parse(packet) == sample()


def test_negative_extremes():
    pos = sample(pan=-170.0, tilt=-90.0)
    parsed = freed.parse(freed.encode(pos))
    assert parsed.pan == pytest.approx(-170.0) and parsed.tilt == pytest.approx(-90.0)


def test_checksum_and_bad_packets():
    packet = bytearray(freed.encode(sample()))
    packet[28] ^= 0xFF
    with pytest.raises(ValueError):
        freed.parse(bytes(packet))
    assert freed.parse(bytes(packet), verify=False).zoom == 12345
    with pytest.raises(ValueError):
        freed.parse(b"\xd1" + b"\x00" * 10)


def test_listener_receives_fake_camera():
    async def run():
        received = []
        transport = await freed.listen(0, lambda pos, addr, t: received.append(pos), host="127.0.0.1")
        port = transport.get_extra_info("sockname")[1]
        cam = FakeCamera(freed_target=("127.0.0.1", port), freed_rate=100)
        await cam.start(port=0)
        cam.state.pan = 21.5
        try:
            await asyncio.sleep(0.3)
        finally:
            cam.stop()
            transport.close()
        assert len(received) >= 10
        assert received[-1].pan == pytest.approx(21.5, abs=1e-3)

    asyncio.run(run())

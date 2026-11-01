import asyncio

import pytest

from trackxen.devices import visca
from trackxen.sim.fake_camera import UNITS_PER_DEGREE, FakeCamera


def test_nibbles_roundtrip():
    for value in (0, 1, 255, 2720, -1, -2720, 32767, -32768):
        assert visca.from_nibbles(visca.to_nibbles(value)) == value
    assert visca.to_nibbles(-1) == b"\x0f\x0f\x0f\x0f"
    assert visca.from_nibbles(b"\x0f\x0f\x0f\x0f", signed=False) == 0xFFFF


def test_command_bytes():
    assert visca.pan_tilt_stop() == bytes.fromhex("81 01 06 01 01 01 03 03 ff")
    assert visca.pan_tilt_drive(5, 3, visca.PanDir.RIGHT, visca.TiltDir.UP) == bytes.fromhex(
        "81 01 06 01 05 03 02 01 ff")
    assert visca.pan_tilt_drive(99, 0, visca.PanDir.LEFT, visca.TiltDir.STOP)[4:6] == bytes(
        [visca.PAN_SPEED_MAX, 1])
    assert visca.pan_tilt_absolute(-1, 16, 10, 10) == bytes.fromhex(
        "81 01 06 02 0a 0a 0f 0f 0f 0f 00 00 01 00 ff")
    assert visca.zoom_tele(7) == bytes.fromhex("81 01 04 07 27 ff")
    assert visca.zoom_wide(2) == bytes.fromhex("81 01 04 07 32 ff")
    assert visca.zoom_direct(0x4000) == bytes.fromhex("81 01 04 47 04 00 00 00 ff")
    assert visca.preset_recall(5) == bytes.fromhex("81 01 04 3f 02 05 ff")
    assert visca.inq_pan_tilt_position() == bytes.fromhex("81 09 06 12 ff")


def test_framing():
    packet = visca.frame(visca.PT_COMMAND, visca.pan_tilt_stop(), 1)
    assert packet[:8] == bytes.fromhex("01 00 00 09 00 00 00 01")
    assert visca.unframe(packet) == (visca.PT_COMMAND, 1, visca.pan_tilt_stop())
    with pytest.raises(ValueError):
        visca.unframe(b"\x01\x00")


def test_replies():
    assert visca.parse_reply(bytes.fromhex("90 41 ff")).kind is visca.ReplyKind.ACK
    done = visca.parse_reply(bytes.fromhex("90 50 0f 0f 0f 0f 00 00 00 05 ff"))
    assert done.kind is visca.ReplyKind.COMPLETION
    assert visca.decode_pan_tilt(done.data) == (-1, 5)
    err = visca.parse_reply(bytes.fromhex("90 61 41 ff"))
    assert err.kind is visca.ReplyKind.ERROR and err.error == 0x41
    assert err.error_text == "command not executable"
    assert visca.split_messages(bytes.fromhex("90 41 ff 90 51 ff")) == [
        bytes.fromhex("90 41 ff"), bytes.fromhex("90 51 ff")]
    with pytest.raises(ValueError):
        visca.parse_reply(b"\x81\x01\xff")


def test_client_against_fake_camera():
    async def run():
        cam = FakeCamera()
        port = await cam.start(port=0)
        try:
            async with visca.ViscaClient("127.0.0.1", port) as client:
                assert await client.pan_tilt_position() == (0, 0)
                await client.command(visca.pan_tilt_drive(12, 1, visca.PanDir.RIGHT, visca.TiltDir.STOP))
                await asyncio.sleep(0.3)
                await client.command(visca.pan_tilt_stop())
                pan, tilt = await client.pan_tilt_position()
                assert pan > 0 and tilt == 0
                target = 10 * UNITS_PER_DEGREE
                await client.command(visca.pan_tilt_absolute(target, 0, 24, 24), wait_completion=True)
                assert await client.pan_tilt_position() == (target, 0)
                await client.command(visca.zoom_direct(8000))
                for _ in range(40):  # the zoom travels like a real lens (~1.3 s here)
                    if await client.zoom_position() == 8000:
                        break
                    await asyncio.sleep(0.1)
                assert await client.zoom_position() == 8000
                await client.command(visca.preset_set(4))
                await client.command(visca.pan_tilt_absolute(0, 0, 24, 24), wait_completion=True)
                await client.command(visca.preset_recall(4))
                for _ in range(40):
                    if await client.pan_tilt_position() == (target, 0):
                        break
                    await asyncio.sleep(0.1)
                assert await client.pan_tilt_position() == (target, 0)
                await client.stop_all()
        finally:
            cam.stop()

    asyncio.run(run())


def test_client_times_out_without_camera():
    async def run():
        client = visca.ViscaClient("127.0.0.1", 9, timeout=0.2, use_header=False)
        await client.connect()
        try:
            with pytest.raises(visca.ViscaError):
                await client.inquiry(visca.inq_power())
        finally:
            client.close()

    asyncio.run(run())

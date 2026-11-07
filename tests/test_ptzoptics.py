"""PTZOptics G2 support: camera types, plain VISCA over UDP and TCP, and the network video source."""

import asyncio
import time

import numpy as np
import pytest

from trackxen.control.camera import CameraLink, CameraLinkConfig
from trackxen.core.camera_models import MODELS, camera_defaults, stream_url
from trackxen.core.config import CameraConfig, sim_config
from trackxen.devices import visca
from trackxen.sim.fake_camera import FakeCamera, ptzoptics_settings

PTZ = MODELS["ptzoptics_g2"]


def test_ptzoptics_defaults():
    d = camera_defaults("ptzoptics_g2", 3)
    assert d["visca_transport"] == "udp" and d["visca_port"] == 1259 and d["freed_port"] is None
    assert d["video"] == "stream" and len(d["pan_speeds"]) == 24 and len(d["tilt_speeds"]) == 20
    assert d["pan_speeds"][0] == 1.7 and d["pan_speeds"][-1] == 100.0 and d["tilt_speeds"][-1] == 69.9
    cam = CameraConfig(id="cam3", host="192.168.1.60", **d)
    assert cam.video_url == "rtsp://192.168.1.60:554/1"
    assert stream_url("ptzoptics_g2", "10.0.0.5", sub_stream=True) == "rtsp://10.0.0.5:554/2"
    assert camera_defaults("birddog_p400", 2)["freed_port"] == 40002


def test_old_settings_without_header_mean_plain_udp():
    assert CameraConfig(use_header=False).visca_transport == "udp"
    assert CameraConfig(use_header=True).visca_transport == "sony"
    assert CameraConfig().visca_transport == "sony"


def test_sim_config_sets_the_last_cameras_up_as_ptzoptics():
    cams = sim_config(4, ptzoptics=2).cameras
    assert [c.model for c in cams] == ["birddog_p400"] * 2 + ["ptzoptics_g2"] * 2
    assert cams[3].visca_transport == "udp" and cams[3].freed_port is None and cams[3].video == "ndi"
    assert cams[3].visca_port == 52384 and cams[0].freed_port == 40001


def test_plain_udp_client_talks_to_a_ptzoptics_like_camera():
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        port = await cam.start("127.0.0.1", 0)
        cam.state.pan, cam.state.tilt = 10.0, -3.0
        client = visca.open_client("127.0.0.1", port, "udp", timeout=0.5)
        await client.connect()
        try:
            pan, tilt = await client.pan_tilt_position()
            assert pan == round(10.0 * PTZ["units_per_degree"]) and tilt == round(-3.0 * PTZ["tilt_units_per_degree"])
            await client.command(visca.pan_tilt_drive(5, 20, visca.PanDir.RIGHT, visca.TiltDir.UP))
            with pytest.raises(visca.ViscaError, match="syntax"):  # tilt speed 0x17 is beyond this camera
                await client.command(visca.pan_tilt_drive(5, 23, visca.PanDir.RIGHT, visca.TiltDir.UP))
            await client.stop_all()
        finally:
            client.close()
            cam.stop()

    asyncio.run(run())


class LateCompletions(asyncio.DatagramProtocol):
    """A plain-VISCA camera whose earlier command always finishes just as the next question arrives: it
    sends that completion (socket 1) first, then the answer (socket 0). Commands get an ACK, and later a
    completion only for the second of two in a row."""

    def connection_made(self, transport):
        self.transport, self.commands = transport, 0

    def datagram_received(self, data, addr):
        if data[1] == 0x09:
            self.transport.sendto(b"\x90\x51\xff", addr)  # the earlier command finishing right now
            answer = b"\x00\x01\x02\x03\x00\x00\x00\x05" if data[2:4] == b"\x06\x12" else b"\x00\x04\x00\x00"
            self.transport.sendto(b"\x90\x50" + answer + b"\xff", addr)
        else:
            self.commands += 1
            self.transport.sendto(b"\x90\x41\xff", addr)
            if self.commands % 2 == 0:
                self.transport.sendto(b"\x90\x51\xff", addr)


def test_a_late_completion_is_not_taken_for_the_next_answer():
    """Plain VISCA has no sequence numbers: a command's completion that arrives after the next question was
    sent must not be read as that question's answer (73 times in 2 minutes in the 4-camera simulator run)."""
    async def run():
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(LateCompletions, local_addr=("127.0.0.1", 0))
        client = visca.open_client("127.0.0.1", transport.get_extra_info("sockname")[1], "udp", timeout=0.5)
        await client.connect()
        try:
            for _ in range(5):
                await client.command(visca.pan_tilt_drive(1, 1, visca.PanDir.STOP, visca.TiltDir.STOP))
                assert await client.pan_tilt_position() == (0x0123, 0x0005)
                assert await client.zoom_position() == 0x0400
            reply = await client.command(visca.zoom_stop(), wait_completion=True)  # (the ACK, then its completion)
            assert reply.kind is visca.ReplyKind.COMPLETION
        finally:
            client.close()
            transport.close()

    asyncio.run(run())


def test_camera_link_polls_positions_and_keeps_speeds_in_range():
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        port = await cam.start("127.0.0.1", 0)
        cam.state.pan, cam.state.tilt = -20.0, 5.0
        link = CameraLink("cam3", CameraLinkConfig(
            host="127.0.0.1", visca_port=port, transport="udp", units_per_degree=PTZ["units_per_degree"],
            tilt_units_per_degree=PTZ["tilt_units_per_degree"], pan_speed_max=24, tilt_speed_max=20))
        await link.start()
        try:
            for _ in range(50):
                if link.pose_now():
                    break
                await asyncio.sleep(0.05)
            pose = link.pose_now()
            assert pose is not None and abs(pose.pan + 20.0) < 0.1 and abs(pose.tilt - 5.0) < 0.1
            link.request(3, 23)  # the controller asks for more tilt speed than the camera has
            await asyncio.sleep(0.3)
            assert link.visca_ok and not link.last_error  # sent as 20: no error from the camera
            assert cam.state.tilt_rate == PTZ["tilt_speeds"][19]
            await link.goto(0.0, 0.0, pan_speed=24, tilt_speed=24)
            assert not link.last_error
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_tcp_client_reads_replies_split_or_together():
    async def run():
        async def camera(reader, writer):
            while data := await reader.read(64):
                for msg in visca.split_messages(data):
                    if msg[1] == 0x09:  # inquiry: the reply arrives in two pieces
                        writer.write(b"\x90\x50\x00\x01")
                        await writer.drain()
                        await asyncio.sleep(0.01)
                        writer.write(b"\x02\x03\xff")
                    else:  # ACK and completion in one piece
                        writer.write(b"\x90\x41\xff\x90\x51\xff")
                    await writer.drain()

        server = await asyncio.start_server(camera, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = visca.open_client("127.0.0.1", port, "tcp", timeout=0.5)
        await client.connect()
        try:
            assert await client.zoom_position() == 0x0123
            reply = await client.command(visca.zoom_stop(), wait_completion=True)
            assert reply.kind is visca.ReplyKind.COMPLETION
        finally:
            client.close()
            server.close()

    asyncio.run(run())


def test_tcp_client_reports_a_closed_connection():
    async def run():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = visca.open_client("127.0.0.1", port, "tcp", timeout=0.3)
        await client.connect()
        await asyncio.sleep(0.1)  # the camera hung up
        with pytest.raises(visca.ViscaError):
            await client.inquiry(visca.inq_power())
        client.close()
        server.close()

    asyncio.run(run())


@pytest.fixture
def h264_file(tmp_path):
    """A 2-second 640x360 H.264 video (PyAV), with a moving bright square."""
    av = pytest.importorskip("av")
    path = tmp_path / "camera.ts"
    with av.open(str(path), "w", format="mpegts") as out:
        stream = out.add_stream("libx264", rate=30, options={"preset": "ultrafast", "tune": "zerolatency"})
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        for i in range(60):
            image = np.zeros((360, 640, 3), np.uint8)
            image[100:200, 10 * i % 540:10 * i % 540 + 100] = 255
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="bgr24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return path


def test_stream_source_decodes_and_keeps_the_newest_frame(h264_file):
    from trackxen.vision.sources import SourceConfig, StreamSource, open_source

    src = open_source(SourceConfig("cam3", "stream", str(h264_file)))
    assert isinstance(src, StreamSource) and src.loop  # a file plays again from the start
    src.start()
    try:
        deadline = time.monotonic() + 10
        while src.frames_received < 90 and time.monotonic() < deadline:  # past the end of the file once
            time.sleep(0.05)
        connected = src.connected  # False for a moment each time the file is opened again
        while not connected and time.monotonic() < deadline:
            time.sleep(0.005)
            connected = src.connected
        frame = src.latest()
        assert src.frames_received >= 90 and frame is not None and frame.image.shape == (360, 640, 3)
        assert src.size == (640, 360) and src.codec == "h264" and connected
        assert src.decode_ms is not None and src.decode_ms < 50 and frame.measured > 0
        assert src.ndi_frames_dropped == 0 and src.ndi_frames_total >= 90
    finally:
        src.stop()


def test_stream_source_reports_a_camera_that_is_not_there():
    from trackxen.vision.sources import StreamSource

    src = StreamSource("rtsp://127.0.0.1:9/1", timeout=1.0)
    src.start()
    try:
        deadline = time.monotonic() + 8
        while not src.last_error and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "can't open" in src.last_error and not src.connected and src.latest() is None
    finally:
        src.stop()

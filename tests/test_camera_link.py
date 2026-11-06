"""CameraLink against the simulator's FakeCamera: FreeD history, pose_at, absolute moves, failures and
reconnects (the P400 path: FreeD positions; the PTZOptics path: VISCA position inquiries)."""

import asyncio
import socket
import time

import pytest

from trackxen.control import camera as camera_module
from trackxen.control.camera import CameraLink, CameraLinkConfig, Pose
from trackxen.core.camera_models import MODELS
from trackxen.devices import freed, visca
from trackxen.sim.fake_camera import FakeCamera, ptzoptics_settings

PTZ = MODELS["ptzoptics_g2"]
ABSOLUTE, DRIVE, PRESET = b"\x01\x06\x02", b"\x01\x06\x01", b"\x01\x04\x3f"


class Counters:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def add(self, name, n=1):
        self.counts[name] = self.counts.get(name, 0) + n

    def observe(self, name, value):
        pass


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, message, level="info", **fields):
        self.events.append((kind, message, level))


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ptz_link(port: int, **cfg) -> CameraLink:
    return CameraLink("cam3", CameraLinkConfig(
        host="127.0.0.1", visca_port=port, transport="udp", units_per_degree=PTZ["units_per_degree"],
        tilt_units_per_degree=PTZ["tilt_units_per_degree"], pan_speed_max=24, tilt_speed_max=20, **cfg),
        counters=Counters(), log=Log())


def drop(cam: FakeCamera, command: bytes) -> list:
    """The camera never hears `command` (a lost packet); returns the list of dropped messages."""
    dropped, handle = [], cam._handle

    def lossy(msg, addr, seq):
        if msg[1:4] == command:
            dropped.append(msg)
            return
        handle(msg, addr, seq)

    cam._handle = lossy
    return dropped


async def wait_for(check, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        await asyncio.sleep(0.02)
    return check()


def test_freed_feeds_the_history_and_pose_at_interpolates():
    async def run():
        port = free_udp_port()
        cam = FakeCamera(freed_target=("127.0.0.1", port))
        visca_port = await cam.start("127.0.0.1", 0)
        cam.state.pan, cam.state.tilt, cam.state.zoom = 10.0, -2.0, 4000.0
        counters = Counters()
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", visca_port=visca_port, freed_port=port),
                          counters=counters)
        await link.start()
        try:
            assert await wait_for(lambda: len(link.history) >= 5)
            pose = link.pose_now()
            assert (pose.pan, pose.tilt, pose.zoom) == pytest.approx((10.0, -2.0, 4000.0), abs=1e-3)
            assert link.freed_age < 0.5 and counters.counts.get("cam1.position_polls", 0) == 0  # no polling
            link.request(10, 0)  # 11.5 degrees a second to the right
            await asyncio.sleep(0.5)
            link.request(0, 0)
            await asyncio.sleep(0.2)
            hist = list(link.history)
            assert [p.t for p in hist] == sorted(p.t for p in hist)
            a, b = next((a, b) for a, b in zip(hist, hist[1:]) if b.pan > a.pan + 0.05)  # while turning
            mid = link.pose_at((a.t + b.t) / 2)
            assert mid.pan == pytest.approx((a.pan + b.pan) / 2) and mid.t == pytest.approx((a.t + b.t) / 2)
            assert link.pose_at(hist[0].t - 10).pan == hist[0].pan  # older than the history: the oldest
            assert hist[-1].pan > 14.0 and cam.state.pan_rate == 0.0
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_a_failed_absolute_move_or_preset_recall_stops_a_running_drive():
    """K47: goto/recall_preset mark the speed as sent (stopped) before their command; when that command is
    lost, the camera keeps the drive it had, and the stop must still go out."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            for command, move in ((ABSOLUTE, lambda: link.goto(0.0, 0.0)), (PRESET, lambda: link.recall_preset(3))):
                link.request(10, 0)
                assert await wait_for(lambda: cam.state.pan_rate > 0)
                dropped = drop(cam, command)
                with pytest.raises(visca.ViscaError):
                    await move()
                assert dropped
                assert await wait_for(lambda: cam.state.pan_rate == 0.0, 1.0), command  # the stop was sent
                link.request(0, 0)  # (what set_tracking(False) asks for: nothing new to send)
                await asyncio.sleep(0.2)
                assert cam.state.pan_rate == 0.0
                del cam._handle  # (the camera hears everything again)
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_a_failed_zoom_part_of_an_absolute_move_stops_the_zoom_only():
    """K47: the pan/tilt part was taken (the camera is on its way), the zoom part got lost: a zoom that was
    running is stopped, the absolute pan/tilt move is not cancelled."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            link.request(0, 0, 2)
            assert await wait_for(lambda: cam.state.zoom_rate > 0)
            drop(cam, b"\x01\x04\x47")  # zoom direct
            with pytest.raises(visca.ViscaError):
                await link.goto(100.0, 0.0, zoom=8000.0, pan_speed=1)
            assert await wait_for(lambda: cam.state.zoom_rate == 0.0, 1.0)
            assert cam.state.target is not None  # still on its way to pan 100
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_a_drive_whose_answer_was_lost_is_stopped_again():
    """The camera did the drive but its ACK got lost: the link can't know the camera is moving, so when the
    wish goes back to "stopped" the stop must still be sent."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            await asyncio.sleep(0.1)
            handle = cam._handle

            def no_answer_to_drives(msg, addr, seq):
                if msg[1:4] == DRIVE and msg[6] != 3:
                    reply, cam._reply = cam._reply, lambda *a: None
                    try:
                        handle(msg, addr, seq)
                    finally:
                        cam._reply = reply
                else:
                    handle(msg, addr, seq)

            cam._handle = no_answer_to_drives
            link.request(10, 0)
            assert await wait_for(lambda: cam.state.pan_rate > 0)
            link.request(0, 0)  # back to the wish the link last saw acknowledged
            assert await wait_for(lambda: cam.state.pan_rate == 0.0, 2.0)
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def drop_first_stop(cam: FakeCamera) -> list:
    """The camera never hears the first pan/tilt stop drive (one lost packet); later ones get through."""
    dropped, handle = [], cam._handle

    def lossy(msg, addr, seq):
        if msg[1:4] == DRIVE and msg[6] == 3 and msg[7] == 3 and not dropped:
            dropped.append(msg)
            return
        handle(msg, addr, seq)

    cam._handle = lossy
    return dropped


def test_stop_now_sends_a_lost_stop_again_after_an_absolute_move():
    """K47, STOP ALL and the watchdog during an absolute move (goto, preset): the speed sent is already "stopped",
    so only stop_now marking it unsure gets the lost stop sent again; else the camera finishes the move."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            await link.goto(100.0, 0.0, pan_speed=2)
            assert await wait_for(lambda: cam.state.target is not None and cam.state.pan > 0.1)
            dropped = drop_first_stop(cam)
            with pytest.raises(visca.ViscaError):
                await link.stop_now()
            assert dropped
            assert await wait_for(lambda: cam.state.target is None, 1.0)  # the stop went out again
            pan = cam.state.pan
            await asyncio.sleep(0.3)
            assert cam.state.pan == pan
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_close_sends_a_lost_stop_again():
    """Nothing talks to the camera after close() (TrackXen quits, or the camera was removed in Settings): a
    lost stop must be sent again before it returns, or the camera keeps turning and zooming."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        closed = False
        try:
            link.request(10, 0, 2)
            assert await wait_for(lambda: cam.state.pan_rate > 0 and cam.state.zoom_rate > 0)
            dropped = drop_first_stop(cam)
            await link.close()
            closed = True
            assert dropped
            assert cam.state.pan_rate == 0.0 and cam.state.zoom_rate == 0.0
        finally:
            if not closed:
                await link.close()
            cam.stop()

    asyncio.run(run())


def busy_for(cam: FakeCamera, seconds: float) -> list:
    """From now on, for `seconds`, the camera refuses every command with "buffer full" (an error on socket 0, at
    once) and keeps driving as it was; returns the refused messages."""
    refused, handle, until = [], cam._handle, time.monotonic() + seconds

    def busy(msg, addr, seq):
        if msg[1] == 0x01 and time.monotonic() < until:
            refused.append(msg)
            cam._reply(addr, visca.PT_REPLY, b"\x90\x60\x03\xff", seq)
            return
        handle(msg, addr, seq)

    cam._handle = busy
    return refused


def test_close_sends_a_refused_stop_again_until_the_camera_takes_it():
    """A busy camera refuses the stop for a while (buffer full), not just once: close() must not use up its
    tries within a millisecond, or the camera keeps turning and zooming with nothing left to stop it."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        closed = False
        try:
            link.request(10, 0, 2)
            assert await wait_for(lambda: cam.state.pan_rate > 0 and cam.state.zoom_rate > 0)
            refused = busy_for(cam, 0.05)
            await link.close()
            closed = True
            assert 1 <= len(refused) <= 2  # (tried again a little later, not hammered)
            assert cam.state.pan_rate == 0.0 and cam.state.zoom_rate == 0.0
            assert not [e for e in link.log.events if e[2] == "error"]  # (it was taken in the end)
        finally:
            if not closed:
                await link.close()
            cam.stop()

    asyncio.run(run())


def test_close_logs_a_camera_it_could_not_stop():
    """A camera that never takes the stop is left moving: the log must say so (STOP ALL and the watchdog log
    theirs), and closing still ends within a few seconds."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            link.request(10, 0)
            assert await wait_for(lambda: cam.state.pan_rate > 0)
            busy_for(cam, 60.0)
            t0 = time.monotonic()
            await link.close()
            assert time.monotonic() - t0 < 2.5
            assert [e for e in link.log.events if e[2] == "error" and "stop not acknowledged" in e[1]]
        finally:
            cam.stop()

    asyncio.run(run())


def test_closing_a_link_that_was_never_opened_is_quick_and_quiet():
    """A camera without an address in Settings has a link that is never started: closing it (every Apply) has
    nothing to stop and must not wait or log."""
    async def run():
        link = ptz_link(free_udp_port())
        t0 = time.monotonic()
        await link.close()
        assert time.monotonic() - t0 < 0.1 and not link.log.events

    asyncio.run(run())


def test_close_right_after_the_tcp_connection_dropped_still_stops_the_camera():
    """The camera's TCP connection drops while it pans (it keeps panning) and the link hasn't reconnected yet
    when TrackXen quits: close() connects again and sends the stop, instead of failing "not connected"."""
    async def run():
        pan_dirs, connections = [], []

        async def camera(reader, writer):
            connections.append(writer)
            try:
                while data := await reader.read(64):
                    for msg in visca.split_messages(data):
                        if msg[1:4] == DRIVE:
                            pan_dirs.append(msg[6])
                        writer.write(tcp_camera_reply(msg))
                    await writer.drain()
            except OSError:
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(camera, "127.0.0.1", 0)
        link = CameraLink("cam3", CameraLinkConfig(host="127.0.0.1", visca_port=server.sockets[0].getsockname()[1],
                                                   transport="tcp"), log=Log())
        await link.start()
        closed = False
        try:
            link.request(10, 0)
            assert await wait_for(lambda: pan_dirs and pan_dirs[-1] == 2)  # panning right
            connections[-1].close()  # the connection drops; the camera keeps panning
            assert await wait_for(lambda: link.visca._writer is None, 1.0)
            await link.close()
            closed = True
            assert pan_dirs[-1] == 3  # stopped
            assert await wait_for(lambda: all(c.is_closing() for c in connections), 1.0)  # and nothing left open
        finally:
            if not closed:
                await link.close()
            server.close()

    asyncio.run(run())


class MirroredCamera(FakeCamera):
    """A camera whose VISCA pan runs against its picture and FreeD: "right" turns it left, and its VISCA
    positions count to the left (what pan_sign -1 is for)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pan_units = -self.pan_units

    def _execute(self, msg, addr, seq):
        if msg[1:4] == DRIVE and msg[6] in (1, 2):
            msg = msg[:6] + bytes([3 - msg[6]]) + msg[7:]
        super()._execute(msg, addr, seq)


def test_pan_sign_applies_to_positions_too():
    """K48: absolute moves and position replies are in the same VISCA frame, so the sign must apply to both:
    a goto lands where the polled position says, and a positive request turns the reported pan up."""
    async def run(cam: FakeCamera, sign: int):
        link = ptz_link(await cam.start("127.0.0.1", 0), pan_sign=sign)
        await link.start()
        try:
            await link.goto(10.0, 0.0, pan_speed=24)
            assert await wait_for(lambda: cam.state.target is None)
            assert cam.state.pan == pytest.approx(10.0, abs=0.1)  # the picture really turned right
            assert await wait_for(lambda: abs(link.pose_now().pan - 10.0) < 0.1), (sign, link.pose_now())
            link.request(5, 0)
            await asyncio.sleep(0.4)
            link.request(0, 0)
            await asyncio.sleep(0.2)
            assert cam.state.pan > 10.3 and link.pose_now().pan == pytest.approx(cam.state.pan, abs=0.2)
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run(FakeCamera(**ptzoptics_settings()), 1))
    asyncio.run(run(MirroredCamera(**ptzoptics_settings()), -1))


def test_freed_gap_falls_back_to_polling_with_the_same_sign():
    """K48: with pan_sign -1 the poses polled while FreeD is missing match the FreeD ones."""
    async def run():
        port = free_udp_port()
        cam = MirroredCamera(freed_target=("127.0.0.1", port))
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", visca_port=await cam.start("127.0.0.1", 0),
                                                   freed_port=port, pan_sign=-1), counters=Counters())
        cam.state.pan = 12.0
        await link.start()
        try:
            assert await wait_for(lambda: link.pose_now() is not None and link.freed_age < 0.1)
            cam._tasks[-1].cancel()  # FreeD stops: the link asks over VISCA instead
            assert await wait_for(lambda: link.counters.counts.get("cam1.position_polls", 0) >= 3)
            assert link.pose_now().pan == pytest.approx(12.0, abs=0.1)
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_answered_polls_reset_the_failure_count():
    """K50: sporadic lost position inquiries (each followed by answered ones) never add up to "not answering"
    or a reconnect."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        link.visca.timeout = 0.1
        try:
            assert await wait_for(lambda: link.visca_ok)
            client, handle, asked = link.visca, cam._handle, [0]

            def lose_every_third(msg, addr, seq):
                if msg[1:4] == b"\x09\x06\x12":
                    asked[0] += 1
                    if asked[0] % 3 == 0:
                        return
                handle(msg, addr, seq)

            cam._handle = lose_every_third
            assert await wait_for(lambda: asked[0] >= 25, 6.0)
            assert link.counters.counts.get("cam3.visca_errors", 0) >= 7
            assert link.visca is client and link.visca_ok  # no reconnect, still answering
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_answered_polls_mark_the_camera_answering_again():
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            assert await wait_for(lambda: link.visca_ok)
            for _ in range(3):
                link._failed(visca.ViscaError("no reply"))
            assert not link.visca_ok
            assert await wait_for(lambda: link.visca_ok, 1.0)  # an idle camera: only the polls show it is back
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def test_a_reconnect_does_not_cancel_an_absolute_move_but_resends_a_drive():
    """K50: after a reconnect only a wanted movement is sent again; a stop would cancel a goto under way."""
    async def run():
        cam = FakeCamera(**ptzoptics_settings())
        link = ptz_link(await cam.start("127.0.0.1", 0))
        await link.start()
        try:
            await link.goto(150.0, 0.0, pan_speed=1)  # slow: still under way after the reconnect
            sent = len(cam.received)
            await link._reconnect()
            await asyncio.sleep(0.3)
            assert cam.state.target is not None  # still going
            assert not [m for m in cam.received[sent:] if m[1:4] == DRIVE]  # (no stop)
            link.request(10, 0)
            assert await wait_for(lambda: cam.state.pan_rate > 0)
            cam.state.pan_rate = 0.0  # the camera rebooted
            await link._reconnect()
            assert await wait_for(lambda: cam.state.pan_rate > 0, 1.0)  # the drive was sent again
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())


def tcp_camera_reply(msg: bytes) -> bytes:
    """A minimal TCP camera's answer: a position reply of the inquiry's own length (8 pan/tilt nibbles,
    4 zoom nibbles, as a real camera sends them), or ACK + completion for a command."""
    if msg[1] != 0x09:
        return b"\x90\x41\xff\x90\x51\xff"
    nibbles = 8 if msg[2:4] == b"\x06\x12" else 4
    return b"\x90\x50" + bytes(nibbles) + b"\xff"


def test_only_one_reconnect_at_a_time_and_close_leaves_no_connection(monkeypatch):
    """K51: a slow TCP connect must not let more reconnects start (each would leave a connection open)."""
    async def run():
        open_now = set()

        async def camera(reader, writer):
            open_now.add(writer)
            try:
                while data := await reader.read(64):
                    for msg in visca.split_messages(data):
                        writer.write(tcp_camera_reply(msg))
                    await writer.drain()
            except OSError:
                pass
            finally:
                open_now.discard(writer)
                writer.close()

        connect = visca.ViscaTcpClient.connect

        async def slow_connect(self):
            await asyncio.sleep(0.3)
            await connect(self)

        server = await asyncio.start_server(camera, "127.0.0.1", 0)
        link = CameraLink("cam3", CameraLinkConfig(host="127.0.0.1", visca_port=server.sockets[0].getsockname()[1],
                                                   transport="tcp"))
        await link.start()
        monkeypatch.setattr(visca.ViscaTcpClient, "connect", slow_connect)
        try:
            for _ in range(20):  # the camera "stopped answering", and keeps failing while reconnecting
                link._failed(visca.ViscaError("no reply"))
                await asyncio.sleep(0.02)
            assert await wait_for(lambda: link.visca._writer is not None and len(open_now) == 1, 2.0)
            assert await wait_for(lambda: link.visca_ok, 1.0)
        finally:
            await link.close()
        assert await wait_for(lambda: not open_now, 1.0), len(open_now)
        server.close()

    asyncio.run(run())


def test_close_during_a_reconnect_leaves_no_connection(monkeypatch):
    async def run():
        open_now = set()

        async def camera(reader, writer):
            open_now.add(writer)
            await reader.read()
            open_now.discard(writer)
            writer.close()

        connect = visca.ViscaTcpClient.connect

        async def slow_connect(self):
            await asyncio.sleep(0.2)
            await connect(self)

        server = await asyncio.start_server(camera, "127.0.0.1", 0)
        link = CameraLink("cam3", CameraLinkConfig(host="127.0.0.1", visca_port=server.sockets[0].getsockname()[1],
                                                   transport="tcp"))
        monkeypatch.setattr(visca.ViscaTcpClient, "connect", slow_connect)
        link._reconnecting = asyncio.ensure_future(link._reconnect())
        await asyncio.sleep(0.1)  # connected, not finished
        await link.close()
        await asyncio.sleep(0.3)
        assert await wait_for(lambda: not open_now, 1.0)
        server.close()

    asyncio.run(run())


def test_a_freed_port_in_use_still_leaves_the_camera_controllable():
    """K52: start() raises (for the log), but the send and position loops run: requests still move the camera,
    and positions come from VISCA instead."""
    async def run():
        cam = FakeCamera()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("0.0.0.0", 0))
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", visca_port=await cam.start("127.0.0.1", 0),
                                                   freed_port=blocker.getsockname()[1]))
        try:
            with pytest.raises(OSError):
                await link.start()
            link.request(10, 0)
            assert await wait_for(lambda: cam.state.pan_rate > 0, 1.0)
            assert await wait_for(lambda: link.pose_now() is not None, 1.0)
        finally:
            await link.close()
            cam.stop()
            blocker.close()

    asyncio.run(run())


def test_starting_again_opens_only_what_is_missing():
    """The app tries a link whose start failed again later: that must not start a second pair of loops
    or drop the VISCA connection the loops have made meanwhile, and it binds the FreeD port once free."""
    async def run():
        cam = FakeCamera()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("0.0.0.0", 0))
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", visca_port=await cam.start("127.0.0.1", 0),
                                                   freed_port=blocker.getsockname()[1]))
        try:
            with pytest.raises(OSError):
                await link.start()
            tasks, client = list(link._tasks), link.visca
            link.request(10, 0)
            assert await wait_for(lambda: link.visca_ok and cam.state.pan_rate > 0, 1.0)
            with pytest.raises(OSError):
                await link.start()  # the FreeD port is still taken
            blocker.close()
            await link.start()  # now everything is open
            assert link._tasks == tasks and link.visca is client and link._freed_transport is not None
            assert all(not t.done() for t in tasks) and link.visca_ok and cam.state.pan_rate > 0
        finally:
            await link.close()
            cam.stop()
            blocker.close()

    asyncio.run(run())


def test_a_tcp_camera_that_is_off_at_start_is_connected_once_it_answers():
    """K52: a camera that refuses the connection at start (off, booting) is reconnected later."""
    async def run():
        async def camera(reader, writer):
            while data := await reader.read(64):
                for msg in visca.split_messages(data):
                    writer.write(tcp_camera_reply(msg))
                await writer.drain()
            writer.close()

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        link = CameraLink("cam3", CameraLinkConfig(host="127.0.0.1", visca_port=port, transport="tcp"))
        with pytest.raises(OSError):
            await link.start()
        server = await asyncio.start_server(camera, "127.0.0.1", port)
        try:
            assert await wait_for(lambda: link.visca_ok and link.pose_now() is not None, 5.0)
        finally:
            await link.close()
            server.close()

    asyncio.run(run())


class SlowInquiries:
    """A VISCA client whose position answers take a while (FreeD comes back in the meantime)."""

    def __init__(self):
        self.asked = asyncio.Event()
        self.answer = asyncio.Event()

    async def pan_tilt_position(self):
        self.asked.set()
        await self.answer.wait()
        return 160, 0

    async def zoom_position(self):
        return 0

    def close(self):
        pass


def test_a_poll_answered_after_freed_came_back_is_not_added_out_of_order():
    """K54: the history stays in time order (pose_at bisects it)."""
    async def run():
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", freed_port=40001, poll_hz=50))
        link.visca = slow = SlowInquiries()
        task = asyncio.create_task(link._poll_loop())
        try:
            await asyncio.wait_for(slow.asked.wait(), 1.0)
            pos = freed.FreeDPosition(1, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0)
            link._on_freed(pos, ("127.0.0.1", 40001), time.monotonic())
            slow.answer.set()
            await asyncio.sleep(0.1)
            times = [p.t for p in link.history]
            assert times == sorted(times) and [p.pan for p in link.history] == [3.0]
        finally:
            task.cancel()

    asyncio.run(run())


def test_freed_in_the_same_clock_tick_as_a_poll_is_still_the_newer(monkeypatch):
    """On Windows time.monotonic() moves in 15.6 ms steps: FreeD arriving in the same step as an inquiry
    started is newer than the inquiry's answer, and the answer is not added after it."""
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() // 0.015625 * 0.015625)

    async def run():
        link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", freed_port=40001, poll_hz=50))
        link.visca = slow = SlowInquiries()
        task = asyncio.create_task(link._poll_loop())
        try:
            await asyncio.wait_for(slow.asked.wait(), 1.0)
            pos = freed.FreeDPosition(1, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0)
            link._on_freed(pos, ("127.0.0.1", 40001), time.monotonic())  # (most likely the inquiry's own tick)
            slow.answer.set()
            await asyncio.sleep(0.1)
            assert [p.pan for p in link.history] == [3.0]
        finally:
            task.cancel()

    for _ in range(5):  # (FreeD lands in the inquiry's tick in most runs)
        asyncio.run(run())


def test_a_position_too_old_to_trust_is_not_used():
    """K55: when positions stop arriving, the last one isn't taken for the current one."""
    link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1"))
    now = time.monotonic()
    link.history.extend([Pose(now - 5.0, 1.0, 0.0, 0.0), Pose(now - 4.0, 2.0, 0.0, 0.0)])
    assert link.pose_now() is None and link.pose_at(now) is None
    assert link.pose_at(now - 4.5).pan == pytest.approx(1.5)  # (an old frame still gets its old position)
    link.history.append(Pose(now - 0.05, 3.0, 0.0, 0.0))
    assert link.pose_now().pan == 3.0 and link.pose_at(now).pan == 3.0
    assert camera_module.POSE_MAX_AGE >= 1.5  # several lost inquiries in a row are not enough


def test_freed_from_another_sender_is_ignored():
    """N19: a second camera sending FreeD to this camera's port by mistake doesn't mix into its position."""
    log = Log()
    link = CameraLink("cam1", CameraLinkConfig(host="127.0.0.1", freed_port=40001), counters=Counters(), log=log)
    link._freed_ip = "192.168.1.51"
    now = time.monotonic()
    for i, (sender, pan) in enumerate([("192.168.1.51", 10.0), ("192.168.1.52", -45.0), ("192.168.1.51", 10.5),
                                       ("192.168.1.52", -45.0)]):
        link._on_freed(freed.FreeDPosition(1, pan, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0), (sender, 40001), now + i * 0.02)
    assert [p.pan for p in link.history] == [10.0, 10.5]
    assert link.counters.counts["cam1.freed_foreign"] == 2
    assert sum("192.168.1.52" in m for _, m, _ in log.events) == 1  # said once


def test_start_learns_the_cameras_address_for_freed():
    async def run():
        port = free_udp_port()
        cam = FakeCamera(freed_target=("127.0.0.1", port))
        link = CameraLink("cam1", CameraLinkConfig(host="localhost", visca_port=await cam.start("127.0.0.1", 0),
                                                   freed_port=port))
        await link.start()
        try:
            assert link._freed_ip == "127.0.0.1"
            assert await wait_for(lambda: link.freed_age < 0.1)  # the camera's own FreeD is taken
        finally:
            await link.close()
            cam.stop()

    asyncio.run(run())

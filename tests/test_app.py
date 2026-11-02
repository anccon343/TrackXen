"""The core App: settings apply, the safety watchdog and control loop, STOP ALL and commands.

No GPU and no NDI: the vision worker is a stand-in that only records being started and stopped,
and cameras are stub links (or the real CameraLink with the simulator's fake camera where the
network part matters).
"""

import asyncio
import queue
import socket
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trackxen.control.camera import Pose
from trackxen.core import app as app_module
from trackxen.core.access import AccessGuard
from trackxen.core.app import App
from trackxen.core.config import AppConfig, CameraConfig
from trackxen.core.server import create_server
from trackxen.core.session import CameraSession
from trackxen.devices.visca import ViscaError
from trackxen.rules.audio import AudioFollowConfig, MicMapping
from trackxen.sim.fake_camera import FakeCamera


class FakeProcess:
    """The vision worker process: alive until stopped (or until a test 'crashes' it)."""

    def __init__(self, join_takes: float = 0.0):
        self.alive, self.join_takes = True, join_takes

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout=None) -> None:
        time.sleep(min(self.join_takes, timeout or 0.0))  # a worker still loading answers late
        self.alive = False

    def terminate(self) -> None:
        self.alive = False


class Workers:
    """Stands in for vision.worker.start_worker: records every worker started."""

    def __init__(self):
        self.started: list[tuple] = []
        self.join_takes = 0.0

    def __call__(self, cfg):
        worker = (FakeProcess(self.join_takes), queue.Queue(), queue.Queue())
        self.started.append(worker)
        return worker

    @property
    def alive(self) -> list[tuple]:
        return [w for w in self.started if w[0].is_alive()]


class StubLink:
    """Stands in for CameraLink: records what the app asked; stopping can fail or take time."""

    made: list["StubLink"] = []

    def __init__(self, name, cfg, counters=None, log=None):
        self.name, self.cfg = name, cfg
        self.started = self.closed = False
        self.stops: list[float] = []
        self.requests: list[tuple] = []
        self.stop_error: Exception | None = None
        self.stop_takes = self.close_takes = self.start_takes = 0.0
        self.visca_ok, self.freed_age, self.last_error = True, 0.01, ""
        self.commanding, self.last_motion = False, 0.0
        StubLink.made.append(self)

    async def start(self):
        await asyncio.sleep(self.start_takes)
        self.started = True

    async def close(self):
        await asyncio.sleep(self.close_takes)
        self.closed = True

    async def stop_now(self):
        self.requests.append((0, 0, 0))
        await asyncio.sleep(self.stop_takes)
        if self.stop_error:
            raise self.stop_error
        self.stops.append(time.monotonic())

    def request(self, pan, tilt, zoom=0):
        self.requests.append((pan, tilt, zoom))
        self.commanding = (pan, tilt, zoom) != (0, 0, 0)

    def pose_now(self):
        return Pose(time.monotonic(), 0.0, 0.0, 2266.0)

    def pose_at(self, t):
        return Pose(t, 0.0, 0.0, 2266.0)

    async def recall_preset(self, number):
        pass

    async def goto(self, *args, **kwargs):
        pass


@pytest.fixture
def workers(monkeypatch):
    w = Workers()
    monkeypatch.setattr(app_module, "start_worker", w)
    monkeypatch.delenv("TRACKXEN_GPU_LIBS", raising=False)
    monkeypatch.setattr(app_module, "RESTART_FIRST", 0.05)  # watchdog restarts: quick in tests
    return w


@pytest.fixture
def stub_links(monkeypatch):
    StubLink.made = []
    monkeypatch.setattr(app_module, "CameraLink", StubLink)


def config(*ids: str, **fields) -> AppConfig:
    return AppConfig(cameras=[CameraConfig(id=i, name=f"Camera {i}", host="127.0.0.1", **fields) for i in ids])


def make_app(tmp_path, cfg: AppConfig) -> App:
    return App(cfg, data=tmp_path, models=tmp_path, config_path=tmp_path / "settings.json")


def record_events(app: App) -> list[tuple]:
    """Everything logged from now on: (kind, message, level, source)."""
    events, write = [], app.log.event

    def event(kind, message, level="info", **fields):
        events.append((kind, message, level, fields.get("source", "")))
        write(kind, message, level=level, **fields)

    app.log.event = event
    return events


async def until(condition, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def other_mode(app: App) -> AppConfig:
    """The running settings with another performance mode: only vision restarts."""
    cfg = app.config.model_copy(deep=True)
    cfg.performance_mode = "light" if cfg.performance_mode != "light" else "auto"
    return cfg


# --- the safety watchdog and the control loop ---------------------------------------------------


def test_watchdog_restarts_vision_even_when_a_camera_does_not_answer_the_stop(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1", "cam2"))
        await app.start()
        try:
            for s in app.sessions.values():
                s.set_tracking(True)
            app.sessions["cam1"].link.stop_error = ViscaError("no reply from 192.168.1.60:52381")
            workers.started[0][0].alive = False  # the vision worker crashed
            assert await until(lambda: len(workers.started) == 2)  # restarted all the same
            assert app.sessions["cam2"].link.stops  # and the camera that answers was stopped
            assert all(s.no_video for s in app.sessions.values())
            workers.started[1][0].alive = False  # the watchdog is still on duty
            assert await until(lambda: len(workers.started) == 3)
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_crashed_worker_restarts_come_less_often_until_one_runs_a_while(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        events = record_events(app)
        await app.start()
        try:
            for n in range(2, 5):  # a worker that dies at once every time (e.g. its model file is missing)
                workers.started[-1][0].alive = False
                assert await until(lambda n=n: len(workers.started) == n)
            delays = [float(m.split("restarting in ")[1].split(" s")[0]) for k, m, *_ in events if "restarting in" in m]
            assert delays == [0.05, 0.1, 0.2]
            app.worker_started = time.monotonic() - 120  # this one ran fine for two minutes
            workers.started[-1][0].alive = False
            assert await until(lambda: len(workers.started) == 5)
            assert [m for k, m, *_ in events if "restarting in" in m][-1].endswith("in 0.05 s")  # back to the first delay
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_settings_applied_while_a_crashed_worker_waits_leave_one_worker(tmp_path, workers, stub_links, monkeypatch):
    monkeypatch.setattr(app_module, "RESTART_FIRST", 0.3)

    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            workers.started[0][0].alive = False
            assert await until(lambda: app.worker is None)  # the watchdog noticed and waits before restarting
            await app.apply_config(other_mode(app))  # starts a worker meanwhile
            await asyncio.sleep(0.6)  # past the watchdog's restart delay
            assert len(workers.alive) == 1 and app.worker is workers.alive[0]

            class Libs:  # NVIDIA's libraries arriving while a crashed worker waits: the same
                dir, ready, state = tmp_path, True, "ready"

                def stop(self):
                    pass

            app.gpu_libs, app._use_gpu_libs = Libs(), lambda: None
            app.worker[0].alive = False
            assert await until(lambda: app.worker is None)
            app._gpu_libs_ready()
            await asyncio.sleep(0.6)
            assert len(workers.alive) == 1 and app.worker is workers.alive[0]
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_control_loop_keeps_the_other_cameras_going_when_one_fails(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1", "cam2"))
        events = record_events(app)
        await app.start()
        try:
            bad, good = app.sessions["cam1"], app.sessions["cam2"]
            ticks, good_tick = [], good.tick

            def broken(now):
                raise ValueError("math domain error")  # e.g. a target size of 0 sent by a tool

            def counted(now):
                ticks.append(now)
                good_tick(now)

            bad.tick, good.tick = broken, counted
            bad.link.request(8, 0, 0)  # it was panning
            await asyncio.sleep(0.4)
            assert len(ticks) > 5  # the other camera is still controlled
            assert bad.link.requests[-1] == (0, 0, 0)  # the failing one is stopped, not left panning
            errors = [e for e in events if e[2] == "error" and e[3] == "cam1"]
            assert len(errors) == 1 and "math domain error" in errors[0][1]  # logged, but not 50 times a second
        finally:
            await app.stop()

    asyncio.run(scenario())


# --- camera connections ---------------------------------------------------------------------------


def test_a_typo_in_a_camera_address_does_not_stop_the_app(tmp_path, workers):
    async def scenario():
        cfg = AppConfig(cameras=[CameraConfig(id="cam1", host="192.168.1..60"),
                                 CameraConfig(id="cam2", name="Camera 2")])
        app = make_app(tmp_path, cfg)
        await app.start()  # used to raise UnicodeError (a ValueError) and end the program
        try:
            assert set(app.sessions) == {"cam1", "cam2"} and len(workers.alive) == 1
            new = app.config.model_copy(deep=True)
            new.cameras[0].host, new.cameras[1].host = "", "cam..local"
            await app.apply_config(new)  # Save in Settings: the same typo on another camera
            assert set(app.sessions) == {"cam1", "cam2"} and len(workers.alive) == 1
            assert AppConfig.load(tmp_path / "settings.json").cameras[1].host == "cam..local"
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_a_camera_connection_that_could_not_be_opened_is_tried_again(tmp_path, workers, monkeypatch):
    monkeypatch.setattr(app_module, "LINK_RETRY_FIRST", 0.1)

    async def scenario():
        cam = FakeCamera()
        port = await cam.start(port=0)
        freed_port = free_udp_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("0.0.0.0", freed_port))  # another program has the FreeD port for now
        app = make_app(tmp_path, AppConfig(cameras=[
            CameraConfig(id="cam1", host="127.0.0.1", visca_port=port, freed_port=freed_port)]))
        try:
            await app.start()
            link = app.sessions["cam1"].link
            assert not link.visca_ok
            blocker.close()
            assert await until(lambda: link.visca_ok, timeout=4.0)  # connected and answering now
            link.request(0, 5, 0)
            assert await until(lambda: cam.state.tilt > 0)  # and it can be moved
        finally:
            blocker.close()
            await app.stop()
            cam.stop()

    asyncio.run(scenario())


def test_a_camera_id_used_twice_does_not_leave_a_connection_open(tmp_path, workers, stub_links):
    async def scenario():
        cfg = config("cam1")
        cfg.cameras.append(CameraConfig(id="cam1", name="Choir", host="127.0.0.2"))  # (bypasses any validation)
        app = make_app(tmp_path, cfg)
        await app.start()
        try:
            first, second = StubLink.made
            assert first.closed and not second.closed and app.sessions["cam1"].link is second
        finally:
            await app.stop()

    asyncio.run(scenario())


# --- settings apply -------------------------------------------------------------------------------


def test_restarting_vision_does_not_freeze_the_cameras(tmp_path, workers, stub_links):
    workers.join_takes = 1.0  # a worker still loading its model reads the stop late

    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            beats = []

            async def heartbeat():
                while True:
                    beats.append(time.monotonic())
                    await asyncio.sleep(0.02)

            task = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.05)
            await app.apply_config(other_mode(app))
            await asyncio.sleep(0.05)
            task.cancel()
            assert max(b - a for a, b in zip(beats, beats[1:])) < 0.3  # the event loop kept running
            assert len(workers.alive) == 1
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_two_saves_at_once_leave_no_connection_or_worker_behind(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            for link in StubLink.made:
                link.close_takes = 0.05
            a, b = config("cam1", "cam2"), config("cam1", "cam3")
            for cfg in (a, b):
                cfg.cameras[0].name = "Pulpit"
            await asyncio.gather(app.apply_config(a), app.apply_config(b))
            current = [s.link for s in app.sessions.values()]
            assert [lk for lk in StubLink.made if lk not in current and not lk.closed] == []
            assert set(app.sessions) == {"cam1", "cam3"} and len(workers.alive) == 1
            assert app.config_rev == 2
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_an_older_settings_page_keeps_the_live_detection_thresholds(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            stale = app.config.model_copy(deep=True)  # loaded on another tablet earlier
            reply = await app.command({"type": "detection", "settings": {"start_confidence": 0.6}})
            assert reply["ok"]
            await app.apply_config(stale)
            assert app.config.detection.start_confidence == 0.6
            assert AppConfig.load(tmp_path / "settings.json").detection.start_confidence == 0.6
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_a_connections_change_during_a_save_is_kept(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            StubLink.made[0].close_takes = 0.3
            new = app.config.model_copy(deep=True)
            new.cameras[0].name, new.cameras[0].host = "Pulpit", "127.0.0.2"  # the cameras restart
            saving = asyncio.create_task(app.apply_config(new))
            await asyncio.sleep(0.1)  # while it closes the camera connections
            assert (await app.command({"type": "connections", "vmix": {"host": "127.0.0.1", "port": 1}}))["ok"]
            await saving
            assert app.config.vmix.host == "127.0.0.1"
            saved = AppConfig.load(tmp_path / "settings.json")
            assert saved.vmix.host == "127.0.0.1" and saved.cameras[0].name == "Pulpit"
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_every_new_worker_after_the_first_tells_the_sessions(tmp_path, workers, stub_links, monkeypatch):
    told = []
    monkeypatch.setattr(CameraSession, "worker_restarted", lambda self: told.append(self.cfg.id), raising=False)

    async def scenario():
        app = make_app(tmp_path, config("cam1", "cam2"))
        await app.start()
        try:
            assert told == [] and app.config_rev == 0  # the very first worker
            await app.apply_config(other_mode(app))
            assert told == ["cam1", "cam2"] and app.config_rev == 1
            workers.started[-1][0].alive = False
            assert await until(lambda: len(told) == 4)
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_statistics_reset_baseline_starts_over_with_a_new_worker(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            app._on_message({"type": "stats", "camera": "cam1", "fps": 0.0, "ndi_dropped": 120, "ndi_total": 54000})
            app.reset_statistics()
            await app.apply_config(other_mode(app))
            app._on_message({"type": "stats", "camera": "cam1", "fps": 0.0, "ndi_dropped": 80, "ndi_total": 18000})
            cam = app.status()["cameras"][0]
            assert (cam["ndi_dropped"], cam["ndi_total"]) == (80, 18000)
        finally:
            await app.stop()

    asyncio.run(scenario())


# --- STOP ALL -------------------------------------------------------------------------------------


def test_stop_all_holds_against_audio_follow(tmp_path, workers, stub_links):
    async def scenario():
        cfg = config("cam1")
        cfg.audio = AudioFollowConfig(enabled=True, mappings=[MicMapping(channel=1, label="Pastor", presets={"cam1": 2})])
        app = make_app(tmp_path, cfg)
        await app.start()
        try:
            s = app.sessions["cam1"]
            s.set_tracking(True)
            s.cue = ("", "Pastor", time.monotonic() + 8.0, 1)
            app.inputs.follow.waiting[2] = time.monotonic()
            assert (await app.command({"type": "stop_all"}))["ok"]
            assert not s.tracking and s.move is None and s.cue is None and not s.audio_follow
            assert not app.inputs.follow.waiting
            app.inputs._cue(1, time.monotonic())  # the pastor starts speaking again
            assert not s.tracking and s.move is None  # no preset recall, no tracking
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_stop_all_stops_every_camera_at_once(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1", "cam2"))
        await app.start()
        try:
            for s in app.sessions.values():
                s.set_tracking(True)
            dead = app.sessions["cam1"].link
            dead.stop_takes, dead.stop_error = 1.0, ViscaError("no reply from 192.168.1.60:52381")
            pressed = asyncio.create_task(app.command({"type": "stop_all"}))
            await asyncio.sleep(0.15)
            cam2 = app.sessions["cam2"]
            assert not cam2.tracking and cam2.link.stops  # not held up by the camera that doesn't answer
            assert (await pressed)["ok"]
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_stop_all_while_settings_are_applied_keeps_audio_follow_off(tmp_path, workers, stub_links, monkeypatch):
    """The operator's Audio follow and On Air switches carry over to the cameras an Apply makes again,
    and a STOP ALL pressed while the cameras are being started again is not undone by that."""
    async def scenario():
        app = make_app(tmp_path, config("cam1", "cam2"))
        await app.start()
        try:
            app.sessions["cam1"].audio_follow = False
            app.sessions["cam2"].on_air_manual = True
            renamed = app.config.model_copy(deep=True)
            renamed.cameras[0].name = "Pulpit"  # a camera change: the sessions are made again
            await app.apply_config(renamed)
            cam1, cam2 = app.sessions["cam1"], app.sessions["cam2"]
            assert not cam1.audio_follow and cam2.audio_follow and cam2.on_air_manual is True

            async def slow_start(link):
                await asyncio.sleep(0.2)  # each camera connection takes a while to open
                link.started = True

            monkeypatch.setattr(StubLink, "start", slow_start)
            cam1.audio_follow = True
            renamed = app.config.model_copy(deep=True)
            renamed.cameras[1].name = "Choir"
            saving = asyncio.create_task(app.apply_config(renamed))
            assert await until(lambda: "cam1" in app.sessions and app.sessions["cam1"] is not cam1
                               and "cam2" not in app.sessions)
            assert (await app.command({"type": "stop_all"}))["ok"]  # cam1 is made again, cam2 not yet
            await saving
            assert not [s for s in app.sessions.values() if s.audio_follow or s.tracking]
            assert app.sessions["cam2"].on_air_manual is True
        finally:
            await app.stop()

    asyncio.run(scenario())


# --- commands -------------------------------------------------------------------------------------


def test_audio_settings_never_map_a_mic_to_a_deleted_face(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            pastor = app.faces.store.add("Pastor", [0.1] * 4, b"")
            mappings = [{"channel": 1, "label": "Pastor", "face": pastor.id},
                        {"channel": 2, "label": "Guest", "face": "deleted-on-another-tablet"}]
            reply = await app.command({"type": "audio_settings", "settings": {"enabled": True, "mappings": mappings}})
            assert reply["ok"] and [m["face"] for m in reply["audio"]["mappings"]] == [pastor.id, ""]
            assert [m.face for m in AppConfig.load(app.config_path).audio.mappings] == [pastor.id, ""]
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_bad_commands_are_answered_not_raised(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            bad = [{"type": "detection", "settings": {"start_confidence": 5}},
                   {"type": "audio_settings"},
                   {"type": "connections", "vmix": "192.168.1.20"},
                   {"type": "connections", "vmix": {"host": "192.168.1.20"}, "mixer": {"port": "x"}},
                   {"camera": "cam1"},
                   {"type": "slot_settings", "camera": "cam1", "settings": {"fixed_areas": {"0": True}}},
                   {"type": "slot_settings", "camera": "cam1", "settings": ["sensitivity", 5]}]
            for cmd in bad:
                reply = await app.command(cmd)
                assert reply["ok"] is False and reply["error"], cmd
            assert app.config.vmix.host == ""  # nothing half-applied
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_switches_take_only_true_or_false(tmp_path, workers, stub_links):
    async def scenario():
        app = make_app(tmp_path, config("cam1"))
        await app.start()
        try:
            s = app.sessions["cam1"]
            await app.command({"type": "tracking", "camera": "cam1", "on": "false"})  # a quoted Companion value
            assert not s.tracking
            await app.command({"type": "tracking", "camera": "cam1", "on": "true"})
            assert s.tracking
            reply = await app.command({"type": "tracking", "camera": "cam1", "on": "off please"})
            assert not reply["ok"] and s.tracking
            await app.command({"type": "audio_follow", "camera": "cam1", "on": "false"})
            assert not s.audio_follow
            await app.command({"type": "on_air", "camera": "cam1", "on": "false"})
            assert s.on_air_manual is False
            await app.command({"type": "on_air", "camera": "cam1", "on": None})
            assert s.on_air_manual is None
        finally:
            await app.stop()

    asyncio.run(scenario())


def test_hardware_test_says_why_it_waits(tmp_path, workers):
    app = make_app(tmp_path, AppConfig())

    class Libs:
        state, error = "failed", "no internet connection: TrackXen needs to download them once"

    app.gpu_libs = Libs()
    assert not app.run_hardware_test(4, 30.0, False, True)
    text = " ".join(app.hwtest["lines"])
    assert "couldn't be downloaded" in text and "no internet" in text and "still being downloaded" not in text
    Libs.state = "downloading"
    assert not app.run_hardware_test(4, 30.0, False, True)
    assert "still being downloaded" in " ".join(app.hwtest["lines"])


# --- problem lights -------------------------------------------------------------------------------


def test_problem_lights_for_cameras_freed_vmix_and_mixer(tmp_path, workers, stub_links):
    async def scenario():
        cfg = config("cam1", "cam2", "cam3")
        cfg.cameras[0].freed_port, cfg.cameras[1].freed_port = 40101, 40102
        cfg.cameras[2].host = ""  # not set up yet: no light
        cfg.vmix.host, cfg.vmix.port = "127.0.0.1", free_udp_port()  # nobody listens there
        cfg.mixer.host, cfg.mixer.port = "127.0.0.1", free_udp_port()
        app = make_app(tmp_path, cfg)
        await app.start()
        try:
            cam1, cam2 = app.sessions["cam1"].link, app.sessions["cam2"].link
            cam1.visca_ok, cam1.last_error, cam1.freed_age = False, "no reply from 127.0.0.1:52381", float("inf")
            cam2.freed_age = 30.0  # answers VISCA, but FreeD stopped
            app.sessions["cam3"].link.visca_ok = False
            for _ in range(3):
                app.collector.tick()
            problems = {(p["source"], p["level"]): p["message"] for p in app.status()["problems"]}
            assert "not answering" in problems[("cam1", "error")] and "no reply" in problems[("cam1", "error")]
            assert "FreeD" in problems[("cam2", "warning")]
            assert ("vmix", "warning") in problems and ("mixer", "warning") in problems
            assert not [p for p in problems if p[0] == "cam3"] and ("cam1", "warning") not in problems
            cam1.visca_ok, cam1.freed_age, cam2.freed_age = True, 0.01, 0.01
            for _ in range(5):
                app.collector.tick()
            assert not [p for p in app.status()["problems"] if p["source"].startswith("cam")]
        finally:
            await app.stop()

    asyncio.run(scenario())


# --- the web API's origin check -------------------------------------------------------------------


def test_requests_from_another_web_site_are_refused(tmp_path, workers):
    app = make_app(tmp_path, AppConfig())
    # (opened as this PC: the server answers only its own names and addresses)
    client = TestClient(create_server(app, AccessGuard(tmp_path / "access.json")), base_url="http://127.0.0.1:8090",
                        client=("127.0.0.1", 50000))
    stop = {"type": "stop_all"}
    assert client.post("/api/command", json=stop, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/command", json=stop, headers={"Origin": "http://127.0.0.1:8090"}).status_code == 200
    assert client.post("/api/command", json=stop).status_code == 200  # tools and Companion send no Origin
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8090/ws", headers={"Origin": "http://evil.example"}) as ws:
            ws.receive_json()

"""Starting and stopping TrackXen: python -m trackxen (__main__.py) and the Windows app (desktop.py),
with a stand-in for the core so no camera or vision worker is involved."""

import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import trackxen
import trackxen.__main__ as cli
import trackxen.core.app as core_app
import trackxen.core.server as core_server
from trackxen import desktop
from trackxen.core.config import AppConfig

ENGINE = Path(trackxen.__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


answers = desktop._running  # (does something answer on this port of this PC)


class FakeApp:
    """Stands in for App: records start and stop."""

    events: list = []
    start_fails = False
    release: threading.Event | None = None  # start() waits for it (cameras still connecting)

    def __init__(self, cfg, config_path=None):
        self.log = type("Log", (), {"text_path": "-"})()

    async def start(self):
        self.events.append("start")
        if self.release is not None:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        if self.start_fails:
            raise OSError("camera unreachable")

    async def stop(self):
        self.events.append("stop")


@pytest.fixture
def fake_app(monkeypatch):
    class App(FakeApp):
        events = []

    monkeypatch.setattr(cli, "App", App)
    monkeypatch.setattr(core_app, "App", App)
    return App


async def asgi(scope, receive, send):
    """A web app that only answers uvicorn's start and stop."""
    if scope["type"] == "lifespan":
        while True:
            if (await receive())["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return


# --- python -m trackxen ---------------------------------------------------------------------------

def test_port_option_is_not_saved_in_the_settings(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    AppConfig(web_port=8090).save(path)
    got = {}

    async def fake_serve(cfg, config_path, port=None):
        got.update(cfg=cfg, port=port)

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.setattr(sys, "argv", ["trackxen", "--config", str(path), "--port", "9000"])
    assert cli.main() == 0
    assert got["port"] == 9000 and got["cfg"].web_port == 8090  # (App saves this object to settings.json)


def test_serve_builds_the_web_server_before_starting_anything(fake_app, monkeypatch):
    def broken(app):
        raise ValueError("access.json is damaged")

    monkeypatch.setattr(cli, "create_server", broken)
    with pytest.raises(ValueError):
        asyncio.run(cli.serve(AppConfig(), Path("unused.json")))
    assert fake_app.events == []  # no cameras connected, no vision worker left running


def test_serve_stops_the_app_when_starting_fails(fake_app, monkeypatch):
    fake_app.start_fails = True
    monkeypatch.setattr(cli, "create_server", lambda app: asgi)
    with pytest.raises(OSError):
        asyncio.run(cli.serve(AppConfig(), Path("unused.json")))
    assert fake_app.events == ["start", "stop"]


CHILD = """
import asyncio, sys
from pathlib import Path
import trackxen.__main__ as cli
from trackxen.core.config import AppConfig

class App:
    def __init__(self, cfg, config_path=None):
        self.log = type("Log", (), {"text_path": "-"})()
    async def start(self):
        pass
    async def stop(self):
        print("stopping", flush=True)
        for i in range(3):  # like stopping three cameras
            await asyncio.sleep(0.2)
            print(f"camera {i + 1} stopped", flush=True)
        print("app stopped", flush=True)

async def asgi(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            if (await receive())["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return

cli.App, cli.create_server = App, lambda app: asgi
try:
    asyncio.run(cli.serve(AppConfig(web_host="127.0.0.1"), Path("unused.json"), int(sys.argv[1])))
except KeyboardInterrupt:
    print("interrupted", flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="sends POSIX signals")
@pytest.mark.parametrize("first, more", [(signal.SIGINT, []), (signal.SIGTERM, []),
                                         (signal.SIGINT, [signal.SIGINT, signal.SIGTERM])])
def test_ctrl_c_or_sigterm_lets_app_stop_finish(tmp_path, first, more):
    script, port = tmp_path / "child.py", free_port()
    script.write_text(CHILD)
    proc = subprocess.Popen([sys.executable, str(script), str(port)], env={**os.environ, "PYTHONPATH": str(ENGINE)},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        end = time.monotonic() + 20
        while not answers(port):  # (the server listens on --port, not on the settings' web_port)
            if proc.poll() is not None or time.monotonic() > end:
                proc.kill()
                pytest.fail(f"the server did not start: {proc.communicate()[0]}")
            time.sleep(0.05)
        proc.send_signal(first)
        out = ""
        while more and "stopping" not in out and select.select([proc.stdout], [], [], 10)[0]:
            out += proc.stdout.readline()
        for sig in more:  # pressed again while the cameras are being stopped
            proc.send_signal(sig)
        out += proc.communicate(timeout=10)[0]
    finally:
        proc.kill()
    assert "camera 3 stopped\napp stopped" in out and "interrupted" not in out, out
    assert proc.returncode == 0


# --- the Windows app --------------------------------------------------------------------------------

def test_desktop_quit_while_starting_never_serves(fake_app, monkeypatch):
    fake_app.release = threading.Event()
    monkeypatch.setattr(core_server, "create_server", lambda app: asgi)
    port, holder = free_port(), {}
    engine = threading.Thread(target=desktop._engine, args=(AppConfig(web_host="127.0.0.1", web_port=port), None, holder))
    engine.start()
    try:
        end = time.monotonic() + 5
        while fake_app.events != ["start"] and time.monotonic() < end:
            time.sleep(0.01)
        holder["server"].should_exit = True  # what Quit in the tray does, while the cameras still connect
    finally:
        fake_app.release.set()
        engine.join(timeout=5)
    assert not engine.is_alive() and fake_app.events == ["start", "stop"] and not answers(port)


def test_desktop_engine_starts_nothing_when_the_web_server_cant_be_built(fake_app, monkeypatch):
    def broken(app):
        raise ValueError("access.json is damaged")

    monkeypatch.setattr(core_server, "create_server", broken)
    holder = {}
    with pytest.raises(ValueError):
        desktop._engine(AppConfig(web_host="127.0.0.1", web_port=free_port()), None, holder)
    assert fake_app.events == [] and holder["error"] == "access.json is damaged"


@pytest.fixture
def desktop_run(monkeypatch, tmp_path):
    """desktop.main() on a data folder of its own, without browser or tray; returns what it told the user."""
    port = free_port()
    (tmp_path / "settings.json").write_text(json.dumps({"web_host": "127.0.0.1", "web_port": port}))
    monkeypatch.setenv("TRACKXEN_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["trackxen", "--no-browser"])
    monkeypatch.setitem(sys.modules, "pystray", None)  # (importing it fails: no tray icon may be shown)
    messages = []
    monkeypatch.setattr(desktop, "_message", messages.append)
    return port, messages


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_desktop_shows_no_tray_icon_when_the_engine_cant_start(desktop_run, monkeypatch):
    def damaged(cfg, config_path=None):
        raise ValueError("faces.json is damaged")

    monkeypatch.setattr(core_app, "App", damaged)
    port, messages = desktop_run
    t0 = time.monotonic()
    assert desktop.main() == 1
    assert time.monotonic() - t0 < 5 and len(messages) == 1 and "faces.json is damaged" in messages[0]


def test_desktop_single_instance_lock(tmp_path):
    first = desktop._single_instance(tmp_path / "trackxen.lock")
    assert first is not None
    assert desktop._single_instance(tmp_path / "trackxen.lock") is None  # a second copy, still starting
    first.close()  # (the first copy ended)
    again = desktop._single_instance(tmp_path / "trackxen.lock")
    assert again is not None
    again.close()


def test_desktop_second_launch_starts_no_second_engine(desktop_run, fake_app, tmp_path):
    port, messages = desktop_run
    lock = desktop._single_instance(tmp_path / "trackxen.lock")  # the first copy, still connecting cameras
    try:
        assert desktop.main() == 0
    finally:
        lock.close()
    assert fake_app.events == [] and messages == []


def test_desktop_says_so_when_another_program_has_the_port(desktop_run, fake_app):
    port, messages = desktop_run
    with socket.socket() as other:
        other.bind(("127.0.0.1", port))
        other.listen()
        assert desktop.main() == 1
    assert fake_app.events == [] and len(messages) == 1 and str(port) in messages[0]

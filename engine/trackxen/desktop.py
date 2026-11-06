"""TrackXen as a Windows app (TrackXen.exe when installed; python -m trackxen.desktop in development).

The engine and web UI run in the background with an icon in the notification area (open
TrackXen, open the logs folder, quit), and the UI opens in the browser. Tablets and phones on
the same network open http://<this PC>:8090. Starting it again while it runs just opens the UI.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP = "TrackXen"


def _quiet_console(log_dir: Path) -> None:
    """A windowed app has no console: send stray prints and tracebacks to a file instead."""
    if sys.stdout is None or sys.stderr is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the app
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out


def _running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_running(port: int, alive=lambda: True, seconds: float = 20.0) -> bool:
    """Wait until the web server answers; False if it doesn't in time, or `alive()` turns False."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _running(port):
            return True
        if not alive():
            return False
        time.sleep(0.2)
    return False


def _single_instance(path: Path):
    """Take this data folder's lock (held as long as the returned file stays open); None if another
    TrackXen has it. The web port can't tell: it answers only after the cameras are connected and
    the vision worker started, so two quick launches would both start."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(path, "a+b")  # noqa: SIM115 - stays open (locked) as long as the app runs
    try:
        if sys.platform == "win32":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def _message(text: str) -> None:
    """Tell the person who started TrackXen (a windowed app has no console)."""
    print(text, file=sys.stderr)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, APP, 0x10)  # (MB_ICONERROR)


def _icon_image():
    """The tray icon, drawn here (no image file needed): an orange ring on dark, like the UI."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(28, 28, 28, 255), outline=(217, 115, 13, 255), width=6)
    d.ellipse((24, 24, 40, 40), fill=(217, 115, 13, 255))
    return img


def _engine(cfg, config_path: Path, holder: dict) -> None:
    """The engine thread: the core and its web server. Once the core starts (cameras, vision
    worker), it is stopped again however this ends; what went wrong goes to holder["error"]."""
    import uvicorn

    from trackxen.core.app import App
    from trackxen.core.server import create_server

    async def run() -> None:
        app = App(cfg, config_path=config_path)
        server = uvicorn.Server(uvicorn.Config(create_server(app), host=cfg.web_host, port=cfg.web_port,
                                               log_level="warning"))
        holder["server"] = server  # (Quit works from here on, also while the cameras are still connecting)

        def close_for_update() -> None:  # an update's setup program runs: close the whole program
            holder["quit"] = True
            server.should_exit = True

        app.on_exit = close_for_update
        try:
            await app.start()
            if not server.should_exit:
                await server.serve()
        finally:
            await app.stop()
            if holder.get("quit") and holder.get("icon"):
                holder["icon"].stop()  # the tray icon too, so the process ends and the update can replace it

    try:
        asyncio.run(run())
    except BaseException as e:
        holder["error"] = ((str(e) or type(e).__name__) if isinstance(e, Exception)
                           else f"the web server did not start on port {cfg.web_port}")  # (uvicorn exits)
        raise


def main() -> int:
    multiprocessing.freeze_support()  # the vision worker is a second copy of this program
    from trackxen.core.config import AppConfig, data_dir

    browser = "--no-browser" not in sys.argv  # (for test runs)
    data = data_dir()
    _quiet_console(data / "logs")
    config_path = data / "settings.json"
    cfg = AppConfig.load(config_path)
    url = f"http://127.0.0.1:{cfg.web_port}"
    lock = _single_instance(data / "trackxen.lock")
    if lock is None:  # TrackXen runs already (or is still starting): just open its UI
        if browser and _wait_running(cfg.web_port):
            webbrowser.open(url)
        return 0
    if _running(cfg.web_port):
        _message(f"TrackXen can't start: another program uses port {cfg.web_port} on this PC.")
        return 1

    import trackxen.core.server  # noqa: F401 - loads the engine here, as before (the wait below is for the server)

    holder: dict = {"lock": lock}  # (the engine thread holds it, so the lock lasts until the engine has ended)
    engine = threading.Thread(target=_engine, args=(cfg, config_path, holder), name="engine")
    engine.start()
    up = _wait_running(cfg.web_port, engine.is_alive)  # open the UI once the server answers (up to ~20 s)
    if not engine.is_alive():  # it couldn't start (e.g. a damaged file): no tray icon for a dead engine
        _message(f"TrackXen could not start: {holder.get('error') or 'it stopped'}\n\nDetails are in {data / 'logs'}")
        return 1
    if up and browser:
        webbrowser.open(url)

    import pystray

    def quit_app(icon, _item) -> None:
        server = holder.get("server")
        if server:
            server.should_exit = True  # the engine then stops every camera and the vision worker
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open TrackXen", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Open the logs folder", lambda *_: os.startfile(data / "logs")),  # noqa: S606
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit TrackXen", quit_app),
    )
    icon = holder["icon"] = pystray.Icon(APP, _icon_image(), f"{APP}: {url}", menu)
    icon.run()
    engine.join(timeout=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())

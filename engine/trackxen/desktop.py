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


def _icon_image():
    """The tray icon, drawn here (no image file needed): an orange ring on dark, like the UI."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(28, 28, 28, 255), outline=(217, 115, 13, 255), width=6)
    d.ellipse((24, 24, 40, 40), fill=(217, 115, 13, 255))
    return img


def main() -> int:
    multiprocessing.freeze_support()  # the vision worker is a second copy of this program
    from trackxen.core.config import AppConfig, data_dir

    browser = "--no-browser" not in sys.argv  # (for test runs)
    data = data_dir()
    _quiet_console(data / "logs")
    config_path = data / "settings.json"
    cfg = AppConfig.load(config_path)
    url = f"http://127.0.0.1:{cfg.web_port}"
    if _running(cfg.web_port):
        if browser:
            webbrowser.open(url)
        return 0

    import uvicorn

    from trackxen.core.app import App
    from trackxen.core.server import create_server

    holder: dict = {}

    def serve() -> None:
        async def run() -> None:
            app = App(cfg, config_path=config_path)
            await app.start()
            server = uvicorn.Server(uvicorn.Config(create_server(app), host=cfg.web_host, port=cfg.web_port,
                                                   log_level="warning"))
            holder["server"] = server
            try:
                await server.serve()
            finally:
                await app.stop()

        asyncio.run(run())

    engine = threading.Thread(target=serve, name="engine")
    engine.start()
    for _ in range(100):  # open the UI once the server answers (up to ~20 s)
        if _running(cfg.web_port):
            if browser:
                webbrowser.open(url)
            break
        time.sleep(0.2)

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
    pystray.Icon(APP, _icon_image(), f"{APP}: {url}", menu).run()
    engine.join(timeout=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())

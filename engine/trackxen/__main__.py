"""Start TrackXen: python -m trackxen [--sim] [--config settings.json] [--port 8090]"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import threading
from pathlib import Path

import uvicorn

from trackxen.core.app import App
from trackxen.core.config import AppConfig, data_dir, sim_config
from trackxen.core.server import create_server


class _Server(uvicorn.Server):
    """uvicorn's server without its own signal handling: once it stops serving, uvicorn raises
    Ctrl+C (or SIGTERM) again, which interrupts App.stop() after its first camera command (or ends
    the process before it), and the cameras keep moving. `stop_signals` is used instead."""

    def capture_signals(self):
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def stop_signals(self):
        """Ctrl+C or SIGTERM ends serving; more of them are ignored until the cameras are stopped."""
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        signals = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if sys.platform == "win32" else [])
        original = {sig: signal.signal(sig, self.handle_exit) for sig in signals}
        try:
            yield
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)


async def serve(cfg: AppConfig, config_path: Path, port: int | None = None) -> None:
    port = port or cfg.web_port  # (--port is for this run only: it is not saved in the settings)
    app = App(cfg, config_path=config_path)
    server = _Server(uvicorn.Config(create_server(app), host=cfg.web_host, port=port, log_level="warning"))
    with contextlib.ExitStack() as signals:
        try:
            await app.start()
            signals.enter_context(server.stop_signals())  # (until app.stop() below has finished)
            print(f"TrackXen running: open http://127.0.0.1:{port}  (log: {app.log.text_path})")
            await server.serve()
        finally:
            await app.stop()


def main() -> int:
    p = argparse.ArgumentParser(prog="trackxen", description=__doc__)
    p.add_argument("--config", type=Path, default=data_dir() / "settings.json")
    p.add_argument("--sim", action="store_true", help="use the virtual camera from tools/sim_camera.py")
    p.add_argument("--sim-cameras", type=int, default=1, help="with --sim: how many virtual cameras (1-4)")
    p.add_argument("--sim-ptzoptics", type=int, default=0,
                   help="with --sim: how many of them (the last ones) are PTZOptics G2 (sim_camera.py --ptzoptics)")
    p.add_argument("--port", type=int)
    args = p.parse_args()
    if args.sim:  # separate settings file, so simulator settings never overwrite the real ones
        name = "settings-sim" + (str(args.sim_cameras) if args.sim_cameras > 1 else "")
        name += f"-ptz{args.sim_ptzoptics}" if args.sim_ptzoptics else ""
        path = data_dir() / f"{name}.json"
        cfg = AppConfig.load(path) if path.exists() else sim_config(args.sim_cameras, args.sim_ptzoptics)
    else:
        path = args.config
        cfg = AppConfig.load(path)
    try:
        asyncio.run(serve(cfg, path, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

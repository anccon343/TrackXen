"""Start TrackXen: python -m trackxen [--sim] [--config settings.json] [--port 8090]"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from trackxen.core.app import App
from trackxen.core.config import AppConfig, data_dir, sim_config
from trackxen.core.server import create_server


async def serve(cfg: AppConfig, config_path: Path) -> None:
    app = App(cfg, config_path=config_path)
    await app.start()
    server = uvicorn.Server(uvicorn.Config(create_server(app), host=cfg.web_host, port=cfg.web_port,
                                           log_level="warning"))
    print(f"TrackXen running: open http://127.0.0.1:{cfg.web_port}  (log: {app.log.text_path})")
    try:
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
    if args.port:
        cfg.web_port = args.port
    try:
        asyncio.run(serve(cfg, path))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

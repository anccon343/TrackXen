"""Shared helpers for the lab tools."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "lab-results"


def results_path(name: str, suffix: str = ".csv") -> Path:
    """A timestamped file in lab-results/, e.g. lab-results/speed-sweep-20261031-2130.csv."""
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return RESULTS_DIR / f"{name}-{stamp}{suffix}"


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def parse_host_port(text: str, default_port: int) -> tuple[str, int]:
    host, _, port = text.partition(":")
    return host, int(port) if port else default_port


def not_simulated(config: dict, cameras: list[str]) -> list[str]:
    """The cameras (ids) that aren't virtual ones from tools/sim_camera.py (NDI "TRACKXEN SIM n" on
    this PC), from TrackXen's settings (GET /api/config). The installed TrackXen answers on the same
    port as `python -m trackxen --sim`, so the simulator tools check this before they move anything."""
    by_id = {c.get("id"): c for c in config.get("cameras", [])}

    def simulated(c: dict) -> bool:
        local = str(c.get("host") or "127.0.0.1").lower() in ("127.0.0.1", "localhost", "::1")
        return local and "TRACKXEN SIM" in str(c.get("ndi") or "").upper()

    return [cam for cam in cameras if not simulated(by_id.get(cam, {}))]

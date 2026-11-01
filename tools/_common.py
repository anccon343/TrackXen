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

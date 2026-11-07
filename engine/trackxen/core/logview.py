"""Reading the daily logs back for the statistics log viewer: which days exist, the day's events,
and its once-a-second samples thinned out to a few thousand points for the graphs."""

from __future__ import annotations

import itertools
import json
import math
import re
from pathlib import Path

DAY = re.compile(r"^\d{8}$")
KINDS = {"text": "TrackXen-{day}.log", "events": "events-{day}.jsonl", "stats": "stats-{day}.jsonl"}


def check_day(day: str) -> str:
    if not DAY.match(day):
        raise ValueError("day must be YYYYMMDD")
    return day


def days(folder: Path) -> list[dict]:
    """Newest first, with the size of each day's files."""
    found: dict[str, int] = {}
    for path in folder.glob("*-????????.*"):
        day = path.stem.rsplit("-", 1)[-1]
        if DAY.match(day):
            found[day] = found.get(day, 0) + path.stat().st_size
    return [{"day": d, "bytes": found[d]} for d in sorted(found, reverse=True)]


def file_of(folder: Path, day: str, kind: str) -> Path:
    return folder / KINDS[kind].format(day=check_day(day))


def events(folder: Path, day: str) -> list[dict]:
    path = file_of(folder, day, "events")
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a line cut off by a crash or power loss
    return out


def _count_lines(f) -> int:
    count, last = 0, b"\n"
    while chunk := f.read(1 << 20):
        count += chunk.count(b"\n")
        last = chunk[-1:]
    return count + (last != b"\n")  # (the last line may have no line end)


def stats(folder: Path, day: str, max_points: int = 2000) -> list[dict]:
    """The day's samples, every n-th one so at most `max_points`; per camera: frames and detections
    per second, detection time and frame-to-result time (medians).

    The file is read a piece at a time, never whole: a day with four cameras is about 250 MB, and
    this runs in the process that drives the cameras."""
    path = file_of(folder, day, "stats")
    if not path.exists():
        return []
    out = []
    with path.open("rb") as f:
        total = _count_lines(f)
        step = max(1, math.ceil(total / max(1, max_points)))
        f.seek(0)
        lines = [line.decode("utf-8", errors="replace") for line in itertools.islice(f, 0, total, step)]
    for line in lines:
        try:
            s = json.loads(line)
        except ValueError:
            continue
        gpu = (s.get("gpus") or [{}])[0]
        app = s.get("app", {})
        cams: dict[str, dict] = {}
        for key, value in app.items():
            cam, _, name = key.partition(".")
            if cam != "app" and name in ("source_fps", "detections_per_s", "detect_ms_p50", "pipeline_ms_p50",
                                         "visca_errors_per_s"):
                cams.setdefault(cam, {})[name] = value
        out.append({"time": s.get("time", ""), "cpu": s.get("cpu_percent"), "app_cpu": s.get("app_cpu_percent"),
                    "gpu": gpu.get("load_percent"), "gpu_temp": gpu.get("temperature_c"),
                    "gpu_mem": gpu.get("memory_used_mb"), "cams": cams})
    return out

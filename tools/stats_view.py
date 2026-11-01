"""Summarise a statistics log afterwards: load per metric (min / average / max) and the event timeline.

  python tools/stats_view.py data/logs                 # newest day in the folder
  python tools/stats_view.py data/logs --day 20261031
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def flatten(record: dict, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[name] = float(value)
        elif isinstance(value, dict):
            out.update(flatten(value, name + "."))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.update(flatten(item, f"{name}{i}."))
    return out


def main(args) -> int:
    folder = Path(args.folder)
    days = sorted(p.stem.split("-")[-1] for p in folder.glob("stats-*.jsonl"))
    if not days:
        print(f"no statistics logs in {folder}")
        return 1
    day = args.day or days[-1]
    stats_file, events_file = folder / f"stats-{day}.jsonl", folder / f"events-{day}.jsonl"

    series: dict[str, list[float]] = {}
    first = last = None
    for line in stats_file.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        first = first or record.get("time")
        last = record.get("time")
        for name, value in flatten(record).items():
            series.setdefault(name, []).append(value)
    print(f"{stats_file.name}: {len(next(iter(series.values()), []))} samples, {first} .. {last}\n")
    print(f"{'metric':40s} {'min':>10s} {'average':>10s} {'max':>10s}")
    for name in sorted(series):
        values = series[name]
        print(f"{name:40s} {min(values):10.1f} {sum(values) / len(values):10.1f} {max(values):10.1f}")

    if events_file.exists():
        print(f"\nevents ({events_file.name}):")
        for line in events_file.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            print(f"  {e['time'][11:23]}  {e['level']:7s} {e.get('source', ''):6s} {e['kind']}: {e['message']}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", help="folder with stats-*.jsonl and events-*.jsonl")
    p.add_argument("--day", help="YYYYMMDD (default: newest)")
    sys.exit(main(p.parse_args()))

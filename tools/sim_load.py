"""Load and soak test: every camera tracks at once, for minutes or hours; reports what was measured.

  python tools/sim_camera.py --camera 1      (and --camera 2, 3, 4 in more windows)
  python -m trackxen --sim --sim-cameras 4
  python tools/sim_load.py --minutes 10

Every camera gets auto select on and Auto Tracking on. Once a second the status is read; at the
end it prints per camera: target visible, detections per second, detection and frame-to-result
times, frames skipped; for the PC: CPU, GPU, memory growth; and the warnings/errors in the log.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request


def get(url: str) -> dict:
    return json.load(urllib.request.urlopen(url, timeout=5))


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=5))


def pct(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * q))] if values else float("nan")


def main(args) -> int:
    base = f"http://127.0.0.1:{args.port}"
    status = get(f"{base}/api/status")
    cams = [c["id"] for c in status["cameras"]]
    for cam in cams:
        post(f"{base}/api/command", {"type": "slot_settings", "camera": cam,
                                     "settings": {"auto_select": {"enabled": True, "start_time": 1.0}}})
        post(f"{base}/api/command", {"type": "tracking", "camera": cam, "on": True})
    print(f"{len(cams)} cameras tracking; measuring for {args.minutes} min", flush=True)
    rows = {c: {"visible": 0, "samples": 0, "det": [], "detect": [], "pipe": [], "skipped": [], "states": set()} for c in cams}
    pc = {"cpu": [], "gpu": [], "app_mem": [], "worker_alive": 0}
    start, last_report = time.monotonic(), time.monotonic()
    try:
        while time.monotonic() - start < args.minutes * 60:
            status = get(f"{base}/api/status")
            app = status["stats"].get("app", {})
            pc["worker_alive"] += 0 if status["worker_alive"] else 1
            if status["stats"]:
                pc["cpu"].append(status["stats"]["cpu_percent"])
                pc["gpu"].append(status["stats"]["gpus"][0]["load_percent"] if status["stats"]["gpus"] else 0)
                pc["app_mem"].append(status["stats"]["app_memory_mb"])
            for cam in status["cameras"]:
                r = rows[cam["id"]]
                r["samples"] += 1
                r["states"].add(cam["state"])
                r["visible"] += any(t["id"] == cam["target"] and not t["missed"] for t in cam["tracks"])
                for key, out in (("detections_per_s", "det"), ("detect_ms_p50", "detect"),
                                 ("pipeline_ms_p50", "pipe"), ("skipped_frames_per_s", "skipped")):
                    value = app.get(f"{cam['id']}.{key}")
                    if value is not None:
                        r[out].append(value)
            if time.monotonic() - last_report > 60:
                last_report = time.monotonic()
                print(f"  {int((time.monotonic() - start) / 60)} min: CPU {pc['cpu'][-1]:.0f}%  GPU {pc['gpu'][-1]}%  "
                      f"app memory {pc['app_mem'][-1]} MB", flush=True)
            time.sleep(1.0)
    finally:
        for cam in cams:
            post(f"{base}/api/command", {"type": "tracking", "camera": cam, "on": False})
    print(f"\n{'camera':8s} {'visible':>8s} {'det/s':>6s} {'detect ms p50/p95':>18s} {'frame->result p50/p95':>22s} {'skipped/s':>9s}")
    for cam, r in rows.items():
        print(f"{cam:8s} {r['visible'] / max(1, r['samples']):8.0%} {statistics.median(r['det'] or [0]):6.1f} "
              f"{statistics.median(r['detect'] or [0]):8.1f} / {pct(r['detect'], 0.95):5.1f}   "
              f"{statistics.median(r['pipe'] or [0]):10.1f} / {pct(r['pipe'], 0.95):5.1f}   "
              f"{statistics.median(r['skipped'] or [0]):9.1f}  states {sorted(r['states'])}")
    mem = pc["app_mem"]
    print(f"\nPC: CPU median {statistics.median(pc['cpu'] or [0]):.0f}% (95th {pct(pc['cpu'], 0.95):.0f}%), "
          f"GPU median {statistics.median(pc['gpu'] or [0]):.0f}% (95th {pct(pc['gpu'], 0.95):.0f}%)")
    if mem:
        print(f"TrackXen memory: {mem[0]} MB at the start, {mem[-1]} MB at the end, highest {max(mem)} MB")
    print(f"vision worker down in {pc['worker_alive']} samples")
    day = time.strftime("%Y%m%d")
    events = get(f"{base}/api/logs/{day}/events")
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - (time.monotonic() - start)))
    bad = [e for e in events if e["time"] >= since and e["level"] in ("warning", "error")]
    print(f"log: {len(bad)} warnings/errors during the run")
    for e in bad[-15:]:
        print(f"  {e['time'][11:19]} {e['level']:7s} {e.get('source', ''):5s} {e['kind']}: {e['message']}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--minutes", type=float, default=5.0)
    sys.exit(main(p.parse_args()))

"""End-to-end check against the virtual camera: lock onto the walking person and measure framing.

  python tools/sim_camera.py          (window 1)
  python -m trackxen --sim            (window 2)
  python tools/sim_test.py --seconds 30

Reads TrackXen's own status (the same data the UI shows) ten times a second and reports how far
the target's head stayed from the framing point, how much the camera moved, and any problems.
It refuses a camera that isn't the simulator's (the installed TrackXen answers on the same port),
unless --yes is given.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request

from _common import not_simulated

from trackxen.control.controller import head_point


def get(url: str) -> dict:
    return json.load(urllib.request.urlopen(url, timeout=2))


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=2))


def main(args) -> int:
    base = f"http://127.0.0.1:{args.port}"  # "localhost" tries IPv6 first: ~2 s per request on Windows
    for _ in range(60):  # wait for detections
        try:
            cam = get(f"{base}/api/status")["cameras"][0]
            if cam["tracks"]:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        print("no people detected within 60 s")
        return 1
    camera = cam["id"]
    if not_simulated(get(f"{base}/api/config"), [camera]) and not args.yes:
        print(f"{camera} isn't the simulator's camera (tools/sim_camera.py): the TrackXen on port {args.port} "
              "drives real cameras. Add --yes to move it anyway.")
        return 2
    track = max(cam["tracks"], key=lambda t: t["box"][3] - t["box"][1])
    x = (track["box"][0] + track["box"][2]) / 2 / cam["frame"]["width"]
    y = (track["box"][1] + track["box"][3]) / 2 / cam["frame"]["height"]

    errors_x, errors_y, pans, states, commands = [], [], [], [], []
    try:  # tracking goes off again whatever happens
        print("select:", post(f"{base}/api/command", {"type": "select", "camera": camera, "x": x, "y": y}))
        print("tracking:", post(f"{base}/api/command", {"type": "tracking", "camera": camera, "on": True}))
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            status = get(f"{base}/api/status")
            cam = status["cameras"][0]
            states.append(cam["state"])
            commands.append(tuple(cam["command"]))
            if cam["pose"]:
                pans.append(cam["pose"]["pan"])
            target = next((t for t in cam["tracks"] if t["id"] == cam["target"]), None)
            if target and not target["missed"]:
                hx, hy = head_point(target["box"], target["keypoints"])
                errors_x.append(hx / cam["frame"]["width"] - cam["framing"]["anchor_x"])
                errors_y.append(hy / cam["frame"]["height"] - cam["framing"]["anchor_y"])
            time.sleep(0.1)
    finally:
        post(f"{base}/api/command", {"type": "tracking", "camera": camera, "on": False})

    ax = [abs(e) for e in errors_x]
    print(f"samples with target visible: {len(errors_x)} of {len(states)}")
    if not ax:
        print("the target was never visible: no framing figures")
        return 1
    print(f"horizontal framing error (fraction of width): median {statistics.median(ax):.3f}, "
          f"95th {sorted(ax)[int(len(ax) * 0.95)]:.3f}, max {max(ax):.3f}")
    print(f"vertical framing error (fraction of height): median {statistics.median(abs(e) for e in errors_y):.3f}")
    if pans:
        print(f"camera pan range covered: {min(pans):.1f} .. {max(pans):.1f} degrees")
    print(f"states seen: {sorted(set(states))}; command changes: "
          f"{sum(1 for a, b in zip(commands, commands[1:]) if a != b)}")
    stats = status["stats"].get("app", {})
    for key in ("detections_per_s", "detect_ms_p50", "pipeline_ms_p50", "visca_rtt_ms_p50", "visca_errors_per_s",
                "freed_packets_per_s"):
        print(f"  {cam['id']}.{key}: {stats.get(cam['id'] + '.' + key)}")
    print("problems:", status["problems"] or "none")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--yes", action="store_true", help="also when the camera isn't the simulator's (it moves a real camera)")
    sys.exit(main(p.parse_args()))

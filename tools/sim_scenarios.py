"""End-to-end checks of the Canon tracking rules against the virtual camera.

  python tools/sim_camera.py          (window 1)
  python -m trackxen --sim            (window 2)
  python tools/sim_scenarios.py       (all scenarios, about 4 minutes)
  python tools/sim_scenarios.py fixed halting

Each scenario sets up App Settings slot 5 through TrackXen's own API (as the UI does), watches
the status for a while and prints PASS or FAIL with what it saw. The virtual speaker walks
between pan -22, 0 (6 s), 20 (4 s) and 6 (2 s) degrees in a 36-second loop; a second person
stands still at pan 38. Settings are never saved, so the simulator's settings file is untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request

from trackxen.control.controller import head_point
from trackxen.control.lens import Lens
from trackxen.devices import visca

SLOT = 4  # App Settings 5: scratch space for these tests
LENS = Lens()
WIDE = {"left": -30, "right": 30, "top": 10, "bottom": -14}


class Api:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"  # "localhost" tries IPv6 first: ~2 s per request on Windows
        self.camera = ""

    def get(self, path: str) -> dict:
        return json.load(urllib.request.urlopen(self.base + path, timeout=3))

    def cmd(self, kind: str, **body) -> dict:
        data = json.dumps({"type": kind, "camera": self.camera, **body}).encode()
        req = urllib.request.Request(self.base + "/api/command", data, {"Content-Type": "application/json"})
        reply = json.load(urllib.request.urlopen(req, timeout=3))
        if not reply.get("ok"):
            raise RuntimeError(f"{kind}: {reply}")
        return reply

    def cam(self) -> dict:
        return self.get("/api/status")["cameras"][0]


def head_pan(cam: dict, track: dict) -> float:
    x, y = head_point(track["box"], track["keypoints"])
    dpan, _ = LENS.pixel_to_angles(x, y, cam["frame"]["width"], cam["frame"]["height"], cam["pose"]["zoom"])
    return cam["pose"]["pan"] + dpan


def fresh_slot(api: Api, **settings) -> None:
    """Slot 5 back to defaults plus `settings`, the camera at a wide start view, tracking off."""
    api.cmd("tracking", on=False)
    api.cmd("clear_target")
    api.cmd("slot", index=SLOT)
    api.cmd("initialize", scope="slot")
    api.cmd("slot_settings", settings={"name": "Scenario test", **settings})
    api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(50.0))
    time.sleep(3.0)


def watch(api: Api, seconds: float, every: float = 0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        yield api.cam()
        time.sleep(every)


def wait_for(api: Api, check, seconds: float) -> dict | None:
    for cam in watch(api, seconds):
        if check(cam):
            return cam
    return None


# --- scenarios ------------------------------------------------------------------------------

def auto_select(api: Api) -> tuple[bool, str]:
    """Auto select picks the speaker, never the person in the exclusion area."""
    fresh_slot(api, auto_select={"enabled": True, "start_time": 1.0, "area": {"enabled": True, **WIDE, "right": 45}},
               exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}})
    api.cmd("tracking", on=True)
    cam = wait_for(api, lambda c: c["state"] == "tracking", 40)
    if not cam:
        return False, "never started tracking"
    picked = [head_pan(c, t) for c in watch(api, 15, 0.5) for t in c["tracks"] if t["id"] == c["target"] and not t["missed"]]
    excluded = any(z == "excluded" for z in cam["zones"].values())
    ok = bool(picked) and max(picked) < 30
    return ok, f"target head pan {min(picked):.1f}..{max(picked):.1f}; bystander marked excluded: {excluded}"


def fixed(api: Api) -> tuple[bool, str]:
    """A fixed viewing angle area at pan -5..5 holds its shot while the speaker stands at 0."""
    view = {"set": True, "pan": 0.0, "tilt": -3.0, "zoom": LENS.zoom_for_hfov(30.0)}
    fresh_slot(api, auto_select={"enabled": True, "start_time": 0.5},
               exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}},
               fixed_areas={"0": {"enabled": True, "left": -5, "right": 5, "top": 10, "bottom": -14, "view": view}})
    api.cmd("tracking", on=True)
    states, fixed_pans = set(), []
    for cam in watch(api, 45, 0.2):
        states.add(cam["state"])
        if cam["state"] == "angle_fixed" and not cam["moving"]:
            fixed_pans.append(cam["pose"]["pan"])
    held = bool(fixed_pans) and max(abs(p) for p in fixed_pans) < 0.6
    ok = "angle_fixed" in states and "tracking" in states and held
    span = f"{min(fixed_pans):.2f}..{max(fixed_pans):.2f}" if fixed_pans else "none"
    return ok, f"states {sorted(states)}; pan while fixed {span}"


def halting(api: Api) -> tuple[bool, str]:
    """The speaker walking into a halting area (pan 15..30) is let go; the camera returns to the
    initial position; auto select takes them again once they have walked out."""
    initial = {"set": True, "pan": -2.0, "tilt": -3.0, "zoom": LENS.zoom_for_hfov(55.0)}
    fresh_slot(api, initial=initial, lost_action="return", return_after=1.0,
               auto_select={"enabled": True, "start_time": 1.0},
               exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}},
               halting_areas={"0": {"enabled": True, "left": 15, "right": 30, "top": 10, "bottom": -14}})
    api.cmd("tracking", on=True)
    went_home = released = retaken = False
    last_pan = None  # the target's head while tracked
    for cam in watch(api, 50, 0.2):
        target = next((t for t in cam["tracks"] if t["id"] == cam["target"] and not t["missed"]), None)
        if target and cam["state"] == "tracking":
            if went_home:
                retaken = True
                break
            last_pan = head_pan(cam, target)
        elif cam["target"] is None and last_pan is not None and last_pan > 12:
            released = True  # let go right at the halting area's edge
        if released and cam["state"] == "searching" and not cam["moving"]:
            went_home = went_home or abs(cam["pose"]["pan"] - initial["pan"]) < 0.5
    ok = released and went_home and retaken
    return ok, f"released {released}, back at the initial position {went_home}, tracked again {retaken}"


def shooting_range(api: Api) -> tuple[bool, str]:
    """With the shooting range's right edge at 12 degrees the picture never shows past it."""
    fresh_slot(api, shooting_range={"enabled": True, "left": -40, "right": 12, "top": 30, "bottom": -30},
               auto_select={"enabled": True, "start_time": 0.5},
               exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}})
    api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(24.0))
    time.sleep(2.0)
    api.cmd("tracking", on=True)
    edges = [c["pose"]["pan"] + c["pose"]["hfov"] / 2 for c in watch(api, 40, 0.2)
             if c["state"] in ("tracking", "lost", "searching") and not c["moving"]]
    ok = bool(edges) and max(edges) <= 12.6
    return ok, f"rightmost picture edge {max(edges):.2f} (limit 12)" if edges else "no samples"


async def _drive(pan_dir: visca.PanDir, seconds: float) -> None:
    async with visca.ViscaClient("127.0.0.1") as other:  # another controller, like vMix
        await other.command(visca.pan_tilt_drive(8, 1, pan_dir, visca.TiltDir.STOP))
        await asyncio.sleep(seconds)
        await other.command(visca.pan_tilt_stop())


def manual(api: Api) -> tuple[bool, str]:
    """Another controller moving the camera: tracking stops (restart off) or pauses and resumes (on)."""
    fresh_slot(api)
    api.cmd("tracking", on=True)
    time.sleep(2.0)
    asyncio.run(_drive(visca.PanDir.LEFT, 0.6))
    stopped = wait_for(api, lambda c: not c["tracking"], 3) is not None
    fresh_slot(api, restart_after_manual=True)
    api.cmd("tracking", on=True)
    time.sleep(2.0)
    asyncio.run(_drive(visca.PanDir.RIGHT, 0.6))
    paused = wait_for(api, lambda c: c["paused"], 3) is not None
    resumed = wait_for(api, lambda c: c["tracking"] and not c["paused"], 6) is not None
    return stopped and paused and resumed, f"restart off: stopped {stopped}; restart on: paused {paused}, resumed {resumed}"


async def _store_preset(number: int) -> None:
    async with visca.ViscaClient("127.0.0.1") as client:
        await client.command(visca.preset_set(number - 1))  # VISCA counts presets from 0


def preset_link(api: Api) -> tuple[bool, str]:
    """Preset 2 linked to "On, App Settings 4": recalling it starts tracking with slot 4."""
    fresh_slot(api)
    api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(35.0))
    time.sleep(2.5)
    asyncio.run(_store_preset(2))
    api.cmd("goto", pan=-25.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(50.0))
    time.sleep(3.0)
    api.cmd("preset_link", number=2, auto_tracking="on", slot=3)
    api.cmd("preset", number=2)
    cam = wait_for(api, lambda c: c["tracking"], 12)
    api.cmd("preset_link", number=2, auto_tracking="", slot=0)  # remove the link again
    if not cam:
        return False, "tracking never started"
    ok = cam["slot"] == 3 and abs(cam["pose"]["pan"]) < 0.5
    return ok, f"tracking started with App Settings {cam['slot'] + 1} at pan {cam['pose']['pan']:.2f}"


def multi_person(api: Api) -> tuple[bool, str]:
    """Multi-Person: when the speaker stops at pan 20, the person standing at 38 is framed with them
    (the camera turns to the middle of the two), and the shot goes back to one person after."""
    fresh_slot(api, auto_select={"enabled": True, "start_time": 0.5,
                                 "area": {"enabled": True, "left": -30, "right": 25, "top": 10, "bottom": -14}},
               composition={"display_size": True, "target_height": 0.25},
               auto_framing={"enabled": True, "multi_person": {"enabled": True, "min_size": 0, "transition_speed": 3}})
    api.cmd("tracking", on=True)
    grouped, alone_after = [], False
    for cam in watch(api, 45, 0.2):
        if cam["group"]:
            grouped.append(cam["pose"]["pan"])
        elif grouped and cam["state"] == "tracking":
            alone_after = True
    ok = bool(grouped) and max(grouped) > 24 and alone_after
    return ok, (f"group framed for {len(grouped) / 5:.1f} s, camera up to pan {max(grouped):.1f} "
                f"(speaker at 20, other at 38); back to one person: {alone_after}") if grouped else "never grouped"


def registered_face(api: Api) -> tuple[bool, str]:
    """Register the speaker's face at the podium, then Registered Face Tracking picks them by face
    (from a wide view, nobody clicked). The face is deleted again at the end."""
    fresh_slot(api)
    api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(40.0))
    time.sleep(3.0)
    at_podium = lambda c: any(not t["missed"] and abs(head_pan(c, t)) < 0.6 for t in c["tracks"])  # noqa: E731
    cam = None
    for _ in range(3):  # catch the speaker standing at the podium (they also walk past it without stopping)
        wait_for(api, lambda c: not at_podium(c), 40)
        if wait_for(api, at_podium, 40):
            time.sleep(0.6)
            cam = api.cam()
            if at_podium(cam):
                break
            cam = None
    if not cam:
        return False, "the speaker never stood at the podium"
    t = next(t for t in cam["tracks"] if not t["missed"] and abs(head_pan(cam, t)) < 0.6)
    dpan, dtilt = LENS.pixel_to_angles(*t["keypoints"][0][:2], cam["frame"]["width"], cam["frame"]["height"],
                                       cam["pose"]["zoom"])
    api.cmd("face_guide", on=True)
    face_id = None
    try:
        for hfov in (5.0, 4.5):  # the speaker stands at the podium for 6 s: straight to a face-filling view
            api.cmd("goto", pan=cam["pose"]["pan"] + dpan, tilt=cam["pose"]["tilt"] + dtilt, zoom=LENS.zoom_for_hfov(hfov))
            if wait_for(api, lambda c: (c["face_guide"] or {}).get("ok"), 4.0):
                before = {f["id"] for f in api.get("/api/faces")["faces"]}
                api.cmd("face_capture", name="Scenario speaker")
                wait_for(api, lambda c: not c["face_capturing"], 5)
                face_id = next((f["id"] for f in api.get("/api/faces")["faces"] if f["id"] not in before), None)
                break
    finally:
        api.cmd("face_guide", on=False)
    if not face_id:
        return False, "the face was never good enough to register"
    try:
        api.cmd("slot_settings", settings={"registered_faces": True})
        api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(45.0))
        time.sleep(2.5)
        api.cmd("tracking", on=True)
        cam = wait_for(api, lambda c: c["state"] == "tracking" and c["target_face"] == face_id, 40)
    finally:
        api.cmd("tracking", on=False)
        api.cmd("face_delete", id=face_id)
    return cam is not None, "picked by face from a 45-degree view" if cam else "never picked by face"


def on_air(api: Api) -> tuple[bool, str]:
    """vMix tally (tools/fake_devices.py --no-camera --vmix-input "<this PC> (TRACKXEN SIM 1)" --cycle 8):
    while the camera is on Program it moves more slowly (the on-air top speed is set below the
    speaker's walking speed, so the limit shows)."""
    api.cmd("connections", vmix={"host": "127.0.0.1", "port": 8099})
    vmix = None
    for _ in range(30):
        vmix = api.get("/api/status").get("vmix")
        if vmix and vmix["connected"]:
            break
        time.sleep(0.2)
    if not (vmix and vmix["connected"]):
        return False, "no vMix (start tools/fake_devices.py --no-camera ...)"
    suggestion = vmix["suggested"].get(api.camera)
    if not suggestion:
        return False, "no vMix input matches the simulator's NDI name"
    api.cmd("vmix_input", input=suggestion)
    fresh_slot(api, auto_select={"enabled": True, "start_time": 0.5},
               exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}},
               on_air={"enabled": True, "speed": 0.1, "retarget": "slow"})  # 3 deg/s: below walking speed
    api.cmd("tracking", on=True)
    live, off = [], []
    states = set()
    for cam in watch(api, 45, 0.1):
        states.add(cam["on_air"])
        if cam["state"] == "tracking":
            (live if cam["air"] == 1.0 else off if cam["air"] == 0.0 else []).append(abs(cam["command"][0]))
    api.cmd("vmix_input", input="")
    api.cmd("connections", vmix={"host": ""})
    if states != {True, False} or not live or not off:
        return False, f"tally seen {sorted(states)}; samples live {len(live)}, off {len(off)}"
    ok = max(live) < max(off)
    return ok, f"top pan speed step live {max(live)} vs off air {max(off)} ({len(live)} / {len(off)} samples)"


def audio_follow(api: Api) -> tuple[bool, str]:
    """MR18 audio-follow (fake mixer: channel 1 talks ~4 s on, ~4 s off): when mic 1 starts, the
    camera goes to its preset (the podium) and frames the person there."""
    api.cmd("on_air", on=False)  # deterministic: never "live" during this test
    fresh_slot(api, exclusion_areas={"0": {"enabled": True, "left": 33, "right": 45, "top": 10, "bottom": -14}})
    api.cmd("goto", pan=0.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(35.0))
    time.sleep(2.5)
    asyncio.run(_store_preset(5))
    api.cmd("goto", pan=-30.0, tilt=-3.0, zoom=LENS.zoom_for_hfov(50.0))
    time.sleep(2.5)
    api.cmd("connections", mixer={"host": "127.0.0.1", "port": 10024})
    api.cmd("audio_settings", settings={"enabled": True, "attack": 0.3, "hold": 1.0, "manual_grace": 0.0,
                                         "mappings": [{"channel": 1, "label": "Pastor", "presets": {api.camera: 5}}]})
    try:
        cued = wait_for(api, lambda c: c["cue"] is not None, 20)
        framed = wait_for(api, lambda c: c["tracking"] and c["target"] is not None and c["cue"] is None
                          and abs(c["pose"]["pan"]) < 8, 25)
    finally:
        api.cmd("audio_settings", settings={"enabled": False, "mappings": []})
        api.cmd("connections", mixer={"host": ""})
        api.cmd("on_air", on=None)
    if not cued:
        return False, "mic 1 never cued the camera (start tools/fake_devices.py)"
    return framed is not None, (f"cued by {cued['cue']['label']}; framed person {framed['target']} at pan "
                                f"{framed['pose']['pan']:.1f}") if framed else "cued, but nobody was framed"


SCENARIOS = {"auto_select": auto_select, "fixed": fixed, "halting": halting, "range": shooting_range,
             "manual": manual, "preset": preset_link, "multi": multi_person, "faces": registered_face,
             "onair": on_air, "audio": audio_follow}


def main(args) -> int:
    api = Api(args.port)
    for _ in range(60):
        try:
            api.camera = api.cam()["id"]
            break
        except OSError:
            time.sleep(1)
    else:
        print("TrackXen is not answering")
        return 1
    unknown = [n for n in args.names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s) {unknown}; choose from {list(SCENARIOS)}")
        return 2
    start_slot = api.cam()["slot"]
    failed = 0
    try:
        for name in args.names or SCENARIOS:
            t0 = time.monotonic()
            try:
                ok, detail = SCENARIOS[name](api)
            except Exception as e:  # noqa: BLE001 - report and go on with the next scenario
                ok, detail = False, f"error: {e}"
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name:12s} {detail}  ({time.monotonic() - t0:.0f} s)", flush=True)
    finally:
        api.cmd("tracking", on=False)
        api.cmd("slot", index=SLOT)
        api.cmd("initialize", scope="slot")
        api.cmd("slot", index=start_slot)
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*", help=f"scenarios to run (default: all): {', '.join(SCENARIOS)}")
    p.add_argument("--port", type=int, default=8090)
    sys.exit(main(p.parse_args()))

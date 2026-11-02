"""One camera's tracking: who the target is, and what the camera should do now.

Statuses (Canon's, plus no_video):
- idle: Auto Tracking is off
- no_target: on, Auto Select off, nobody picked yet (the operator clicks a person)
- searching: on, Auto Select is looking for someone
- tracking, or angle_fixed while the target is inside a fixed viewing angle area
- lost: the target hasn't been seen for `lost_after` s; the camera keeps its view or, after
  a while, returns to the initial position
- no_video: no detections arriving; the watchdog stopped the camera

The active App Settings slot decides the rest: areas, composition, sensitivity, the initial
position, and whether tracking restarts after someone moves the camera by hand.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
from dataclasses import dataclass

from trackxen.control.camera import CameraLink, Pose
from trackxen.control.controller import Controller, Observation, SpeedTable, cut_at_bottom
from trackxen.control.kalman import AngleKalman
from trackxen.control.lens import Lens
from trackxen.core.config import CameraConfig
from trackxen.core.tracking_settings import (SLOTS, AppSettings, Area, PresetLink, View, default_slots, merge,
                                             sensitivity_gain, view_area)
from trackxen.rules import areas as rules
from trackxen.rules.framing import Framer, Shot
from trackxen.rules.geometry import area_from_view, area_to_view, view_edges, view_to_room

IDLE, NO_TARGET, SEARCHING, TRACKING, ANGLE_FIXED, LOST, NO_VIDEO = (
    "idle", "no_target", "searching", "tracking", "angle_fixed", "lost", "no_video")
FIXED_EXIT_MARGIN = 0.05  # the target must leave a fixed area by this share of its size (no flapping)
QUIET_AFTER_MOVE = 1.0  # s after our own moves before camera motion counts as someone else's
AIR_BLEND_S = 1.0  # seconds to blend between the normal and the on-air motion
CUE_TIMEOUT = 8.0  # s an audio cue waits for its person to show up
STILL_BEFORE_RESUME = 1.0  # s the camera must be still before tracking restarts after a manual move
AREA_KINDS = ("priority", "fixed", "auto_select", "exclusion", "halting")
PRESETS = 128  # VISCA preset numbers 0..127, shown as 1..128
EDGES = ("left", "right", "top", "bottom")


def _finite(*values: float) -> None:
    """Points and positions from pages and Companion must be numbers (NaN would steer the camera)."""
    if not all(math.isfinite(v) for v in values):
        raise ValueError("positions must be numbers")


@dataclass
class Move:
    """An absolute move this app started: initial position, a fixed viewing angle, View, preset."""

    reason: str
    target: View | None  # None: preset recall (position unknown; done when the camera stops)
    started: float
    deadline: float
    then_track: bool = False  # Camera Preset Link "On": start Auto Tracking when it is done


class CameraSession:
    def __init__(self, cfg: CameraConfig, link: CameraLink, counters=None, log=None):
        self.cfg, self.link, self.counters, self.log = cfg, link, counters, log
        self.lens = Lens(cfg.lens.hfov_wide, cfg.lens.hfov_tele, cfg.lens.zoom_max)
        self.controller = Controller(self.lens, SpeedTable(cfg.pan_speeds, cfg.tilt_speeds))
        self.controller.constrain = self._constrain
        self.selector = rules.AutoSelector()
        self.framer = Framer()  # Auto Framing: what the controller frames (a group, face direction, ...)
        self.head = AngleKalman()  # the target's own head, for re-finding (the controller may frame a group)
        self.shot: Shot | None = None
        self.face_names: dict[str, str] = {}  # registered face id -> name (kept up to date by the app)
        self.target_face: str | None = None  # the target's registered face, once recognised
        self.tracking = False
        self.no_video = False
        self.target_id: int | None = None
        self.stale_id = False  # target_id is from an earlier vision worker (ids start at 1 again): find the person afresh
        self.latest: dict | None = None  # newest "tracks" message
        self.people: list[rules.Placed] = []  # its people, placed in room angles
        self.zones: dict[int, str] = {}  # person -> "halting" / "excluded" (can't be picked)
        self.last_tracks_at = 0.0
        self.last_seen = 0.0  # when the target was last seen
        self.last_seen_captured = 0.0  # capture time of that frame
        self.lost_since: float | None = None  # target lost or released: runs the return-home timer
        self.went_home = False
        self.fixed: int | None = None  # the fixed viewing angle area holding the shot
        self.move: Move | None = None
        self.paused = False  # someone moved the camera by hand: hands off until it is still again
        self.still_since: float | None = None
        self.quiet_until = 0.0
        self.source_stats: dict = {}
        self.engine = ""
        self.manual_until = 0.0
        self._manual_active = False
        self.last_command = (0, 0, 0)
        self.debug: dict = {}
        self.settings_rev = 0  # bumped on every settings change, so pages know to reload them
        self._background: set[asyncio.Task] = set()
        self.counts = {"targets_lost": 0, "refinds": 0, "watchdog_stops": 0, "manual_operations": 0}
        self.on_air_tally = False  # vMix says this camera's input is on Program
        self.on_air_manual: bool | None = None  # the operator's switch (None: follow vMix)
        self.air = 0.0  # 0 = normal motion, 1 = on-air motion (blended)
        self.audio_follow = True  # this camera may answer microphones
        self.manual_at = -1e9  # when the operator last moved this camera or picked its target
        self.cue: tuple[str, str, float, int] | None = None  # audio cue: (face id or "", label, deadline, channel)
        self.framing_mic: int | None = None  # the mic whose person an answered cue found (until the target changes)
        self.refresh()

    @property
    def slot(self) -> AppSettings:
        return self.cfg.slot

    @property
    def on_air(self) -> bool:
        return self.on_air_tally if self.on_air_manual is None else self.on_air_manual

    @property
    def state(self) -> str:
        if not self.tracking:
            return IDLE
        if self.no_video:
            return NO_VIDEO
        if self.target_id is None:  # looking by itself (auto select or registered faces), or waiting for a click
            return SEARCHING if self.slot.auto_select.enabled or self.slot.registered_faces else NO_TARGET
        if self.lost_since is not None:
            return LOST
        return ANGLE_FIXED if self.fixed is not None else TRACKING

    # --- settings ----------------------------------------------------------------------

    def refresh(self) -> None:
        """Apply the active App Settings (after any settings change)."""
        slot = self.slot
        framing = slot.composition
        if not slot.halting_area:  # Pan/Tilt Halting Area off: movement follows the sensitivity alone
            framing = dataclasses.replace(framing, dead_zone_x=0.01, dead_zone_y=0.01)
        self._framing = framing
        self._motion = dataclasses.replace(self.cfg.motion, gain=sensitivity_gain(slot.sensitivity))
        self._apply_air()
        self.controller.fix_tilt = slot.fix_tilt
        self.controller.max_zoom = rules.max_zoom_for_priority(slot, self.lens)
        if self.fixed is not None and (slot.priority_area.enabled or not slot.fixed_areas[self.fixed].enabled):
            self.fixed = None
        self.settings_rev += 1

    def _apply_air(self) -> None:
        """The controller's motion and dead zone: normal, on-air, or a blend while switching."""
        a, oa = self.air, self.slot.on_air
        if not oa.enabled:
            a = 0.0
        m, f = self._motion, self._framing
        scale = 1.0 + (oa.speed - 1.0) * a
        self.controller.motion = dataclasses.replace(
            m, max_pan=m.max_pan * scale, max_tilt=m.max_tilt * scale,
            max_accel=m.max_accel * (1.0 + (oa.accel - 1.0) * a),
            max_zoom_step=m.max_zoom_step if a < 0.5 else (1 if oa.zoom == "slow" else 0))
        grow = 1.0 + (oa.dead_zone - 1.0) * a
        self.controller.framing = dataclasses.replace(f, dead_zone_x=f.dead_zone_x * grow, dead_zone_y=f.dead_zone_y * grow)

    def set_on_air(self, on: bool | None) -> None:
        """The manual on-air switch: True/False, or None to follow vMix again."""
        self.on_air_manual = on
        self._event("on air", "on-air switch: " + ("follow vMix" if on is None else "on" if on else "off"))

    def _holding_back(self) -> bool:
        """Live on Program with "wait": no new targets or big moves until off Program."""
        oa = self.slot.on_air
        return self.on_air and oa.enabled and oa.retarget == "wait"

    def set_slot(self, index: int) -> None:
        if not 0 <= index < SLOTS:
            raise ValueError(f"App Settings slot must be 1..{SLOTS}")
        if index != self.cfg.active_slot:
            self.cfg.active_slot = index
            self.fixed = None
            self.selector.reset()
            self._event("settings", f"App Settings {index + 1} in use: {self.slot.name}")
        self.refresh()

    def update_slot(self, patch: dict, index: int | None = None) -> AppSettings:
        """Change App Settings values (validated). Live at once; Save writes them to disk (so does any
        other change that saves the settings: they are part of the running settings)."""
        i = self.cfg.active_slot if index is None else index
        data = self.cfg.slots[i].model_dump()
        merge(data, patch)
        self.cfg.slots[i] = AppSettings.model_validate(data)
        self.refresh()
        return self.cfg.slots[i]

    def initialize(self, scope: str) -> None:
        """Canon's Initialization: the selected App Settings, or all of them and the preset links."""
        if scope == "all":
            self.cfg.slots = default_slots()
            self.cfg.preset_links = {}
        else:
            self.cfg.slots[self.cfg.active_slot] = default_slots()[self.cfg.active_slot]
        self.fixed = None
        self._event("settings", f"initialized {'all App Settings' if scope == 'all' else 'App Settings ' + str(self.cfg.active_slot + 1)}")
        self.refresh()

    def set_preset_link(self, number: int, auto_tracking: str, slot: int) -> None:
        if not 1 <= number <= PRESETS:
            raise ValueError(f"preset number must be 1..{PRESETS}")
        if auto_tracking == "" and slot == 0:
            self.cfg.preset_links.pop(number, None)
        else:
            self.cfg.preset_links[number] = PresetLink(auto_tracking=auto_tracking, slot=slot)
        self.settings_rev += 1

    def _area(self, kind: str, index: int = 0) -> Area:
        slot = self.slot
        if kind == "priority":
            return slot.priority_area
        if kind == "auto_select":
            return slot.auto_select.area
        lists = {"fixed": slot.fixed_areas, "exclusion": slot.exclusion_areas, "halting": slot.halting_areas}
        if kind not in lists:
            raise ValueError(f"unknown area {kind}")
        return lists[kind][index]

    def set_area(self, kind: str, index: int = 0, rect: list[float] | None = None,
                 enabled: bool | None = None) -> Area:
        """Place an area by a rectangle on the current picture (0..1) and/or switch it on or off.

        An area switched on before it was ever placed starts in the middle of the picture. The
        camera angle it was placed at is kept: View returns there, and a fixed viewing angle
        area holds exactly that shot.
        """
        area = self._area(kind, index)
        on = area.enabled if enabled is None else bool(enabled)
        if rect is None and on and not area.view.set:
            rect = [0.3, 0.3, 0.7, 0.7]
        placed = None
        if rect is not None:  # worked out before anything changes: a refused command leaves the area as it was
            _finite(*rect)
            pose = self.require_pose()
            placed = area_from_view(*rect, pose, self.lens)
        area.enabled = on
        if placed is not None:
            area.left, area.right, area.top, area.bottom = placed.left, placed.right, placed.top, placed.bottom
            area.view = View(set=True, pan=pose.pan, tilt=pose.tilt, zoom=pose.zoom)
        self.refresh()
        return area

    def capture(self, what: str, index: int = 0) -> None:
        """Canon's Capture: the current view becomes the initial position, a shooting range edge,
        or the angle an area shows (for a fixed viewing angle area: the shot it holds)."""
        pose = self.require_pose()
        slot = self.slot
        here = View(set=True, pan=pose.pan, tilt=pose.tilt, zoom=pose.zoom)
        if what == "initial":
            slot.initial = here
        elif what in EDGES:
            setattr(slot.shooting_range, what, view_edges(pose, self.lens)[what])
        elif what in AREA_KINDS:
            self._area(what, index).view = here
        else:
            raise ValueError(f"unknown capture {what}")
        self.refresh()

    def require_pose(self) -> Pose:
        pose = self.link.pose_now()
        if pose is None:
            raise ValueError("the camera's position is not known yet")
        return pose

    def _constrain(self, pan: float, tilt: float, hfov: float, vfov: float) -> tuple[float, float]:
        slot = self.slot
        pan, tilt = rules.keep_priority_in_view(slot, pan, tilt, hfov, vfov)
        return rules.clamp_to_range(slot, pan, tilt, hfov, vfov)  # the shooting range always wins

    # --- operator actions ---------------------------------------------------------

    def set_tracking(self, on: bool, reason: str = "", start_at_initial: bool = True) -> None:
        if not on:  # off also cancels what would switch it on again or pick a target by itself
            if self.move:
                self.move.then_track = False
            self.cue = None
        if on == self.tracking:
            return
        self.tracking = on
        self.controller.reset()
        self.framer.reset()
        self.selector.reset()
        self.paused, self.fixed = False, None
        self.lost_since, self.went_home = None, False
        self._event("tracking", ("auto tracking on" if on else "auto tracking off") + (f": {reason}" if reason else ""))
        if not on:
            self.link.request(0, 0, 0)
        elif start_at_initial and self.target_id is None and self.slot.initial.set:
            self.go_to(self.slot.initial, "initial position")  # Canon: the view tracking starts from

    def select_at(self, x: float, y: float) -> tuple[int | None, str]:
        """Lock onto the person at a point of the picture (0..1 coordinates)."""
        _finite(x, y)
        msg = self.latest
        if not msg or not msg["tracks"]:
            return None, "nobody in the picture"
        px, py = x * msg["width"], y * msg["height"]
        inside = [t for t in msg["tracks"] if t["box"][0] <= px <= t["box"][2] and t["box"][1] <= py <= t["box"][3]]
        if inside:
            pick = min(inside, key=lambda t: (t["box"][2] - t["box"][0]) * (t["box"][3] - t["box"][1]))
        else:
            def dist(t):
                cx, cy = (t["box"][0] + t["box"][2]) / 2, (t["box"][1] + t["box"][3]) / 2
                return ((cx - px) / msg["width"]) ** 2 + ((cy - py) / msg["height"]) ** 2
            pick = min(msg["tracks"], key=dist)
            if dist(pick) > 0.15 ** 2:
                return None, "nobody there"
        if self.zones.get(pick["id"]) == "halting":
            return None, "that person is inside a tracking halting area"
        self.manual_at = time.monotonic()
        self.cue = None  # the operator's pick wins over a microphone still waiting for its person
        self.select_id(pick["id"])
        return pick["id"], ""

    def select_id(self, track_id: int | None, how: str = "selected") -> None:
        self.target_id, self.stale_id = track_id, False
        self._forget_person()
        self.selector.reset()
        self.lost_since, self.went_home, self.fixed = None, False, None
        if track_id is None:
            self._event("target", "target cleared")
            return
        self._event("target", f"target locked: person {track_id} ({how})")
        self.last_seen = time.monotonic()

    def manual(self, pan: int, tilt: int, zoom: int, hold: float = 0.3) -> None:
        """Joystick/buttons in this app: they override tracking while held. With "Restart
        Tracking After Manual Operation" off, a press turns Auto Tracking off (Canon's rule).

        The page repeats the command while a button is held; if it stops repeating (released,
        or the connection dropped), tick() stops the camera when the hold runs out.
        """
        if pan or tilt or zoom:
            self.manual_at, self.cue = time.monotonic(), None
        if (pan or tilt or zoom) and self.tracking and not self.slot.restart_after_manual:
            self._manual_operation("moved with the joystick")
        self.move = None
        self.manual_until = time.monotonic() + hold
        self._manual_active = True
        self._send(pan, tilt, zoom)

    def click_center(self, x: float, y: float) -> None:
        """Canon's Click Centering: turn so this point of the picture (0..1) comes to the middle."""
        _finite(x, y)
        pose = self.require_pose()
        pan, tilt = view_to_room(x, y, pose, self.lens)
        self.go_to_manually(View(set=True, pan=pan, tilt=tilt, zoom=pose.zoom), "click centering")

    def show(self, what: str, index: int = 0) -> None:
        """Canon's View buttons: show the initial position, a shooting range edge or an area."""
        slot = self.slot
        if what == "initial":
            if not slot.initial.set:
                raise ValueError("no initial position captured yet")
            view = slot.initial
        elif what in EDGES:  # put that edge of the picture on the limit
            pose = self.require_pose()
            r, hfov, vfov = slot.shooting_range, self.lens.hfov(pose.zoom), self.lens.vfov(pose.zoom)
            pan = {"left": r.left + hfov / 2, "right": r.right - hfov / 2}.get(what, pose.pan)
            tilt = {"top": r.top - vfov / 2, "bottom": r.bottom + vfov / 2}.get(what, pose.tilt)
            view = View(set=True, pan=pan, tilt=tilt, zoom=pose.zoom)
        else:
            view = rules.view_for_area(self._area(what, index), self.lens)
        self.go_to_manually(view, f"view {what.replace('_', ' ')}" + (f" {index + 1}" if what in ("fixed", "exclusion", "halting") else ""))

    def go_to_manually(self, view: View, reason: str) -> None:
        """An operator-requested absolute move; during tracking it counts as a manual operation."""
        _finite(view.pan, view.tilt, view.zoom)
        self.manual_at, self.cue = time.monotonic(), None
        if self.tracking:
            self._manual_operation(reason)
        self.go_to(view, reason)

    def recall_preset(self, number: int) -> None:
        """Recall camera preset `number` (1.., like BirdUI). Camera Preset Link decides what
        tracking does: On = start tracking with its App Settings once there, Off = stop
        tracking, blank = like any manual operation."""
        if not 1 <= number <= PRESETS:
            raise ValueError(f"preset number must be 1..{PRESETS}")
        self.manual_at, self.cue = time.monotonic(), None  # the operator framed this camera
        link = self.cfg.preset_links.get(number, PresetLink())
        then_track = link.auto_tracking == "on"
        if link.auto_tracking == "off":
            self.set_tracking(False, f"preset {number} recalled (Camera Preset Link: Off)")
        elif then_track:
            self.set_tracking(False, f"preset {number} recalled (Camera Preset Link: On)")
            self.select_id(None)
            self.set_slot(link.slot)
        elif self.tracking:
            self._manual_operation(f"preset {number} recalled")
        now = time.monotonic()
        move = self.move = Move(f"preset {number}", None, now, now + 20.0, then_track)

        def failed() -> None:
            if self.move is move:
                self.move = None

        # the UI counts presets from 1 like BirdUI; VISCA counts from 0 (check in the lab)
        self._spawn(self.link.recall_preset(number - 1), f"preset {number} recall", failed)

    def go_to(self, view: View, reason: str) -> None:
        """Absolute move to a viewing angle at the motion profile's top speed."""
        now = time.monotonic()
        pose = self.link.pose_now()
        m = self.controller.motion  # slower while on air
        pan_step = max(1, SpeedTable.step_for(self.cfg.pan_speeds, m.max_pan))
        tilt_step = max(1, SpeedTable.step_for(self.cfg.tilt_speeds, m.max_tilt))
        duration = 4.0  # allowance for zoom and the camera's reaction
        if pose is not None:
            duration += max(abs(view.pan - pose.pan) / SpeedTable.speed_of(self.cfg.pan_speeds, pan_step),
                             abs(view.tilt - pose.tilt) / SpeedTable.speed_of(self.cfg.tilt_speeds, tilt_step))
        move = self.move = Move(reason, view, now, now + duration)
        self._event("camera", f"moving to {reason}")

        def failed() -> None:
            if self.move is move:
                self.move = None

        self._spawn(self.link.goto(view.pan, view.tilt, view.zoom, pan_step, tilt_step), f"move to {reason}", failed)

    def _forget_person(self) -> None:
        """A new (or no) target: forget the old one's motion, framing and head position."""
        self.controller.reset()
        self.framer.reset()
        self.head.reset()
        self.shot = None
        self.target_face = None
        self.framing_mic = None

    def _manual_operation(self, what: str) -> None:
        self.counts["manual_operations"] += 1
        self.manual_at = time.monotonic()
        if self.slot.restart_after_manual:
            self.paused, self.still_since = True, None
            self._event("tracking", f"{what}: tracking paused until the camera is still")
        else:
            self.set_tracking(False, f"{what} (Restart Tracking After Manual Operation is off)")

    def _spawn(self, coro, what: str, on_error=None) -> None:
        """Run a camera command in the background; failures go to the log."""
        async def run():
            try:
                await coro
            except Exception as e:  # noqa: BLE001 - camera unreachable: report it, keep running
                self._event("camera", f"{what} failed: {e}", level="error")
                if on_error:
                    on_error()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop (unit tests): nothing to run it on
            coro.close()
            return
        task = loop.create_task(run())
        self._background.add(task)  # keep a reference until it is done
        task.add_done_callback(self._background.discard)

    # --- vision input ------------------------------------------------------------------

    def on_tracks(self, msg: dict) -> None:
        now = time.monotonic()
        self.latest, self.last_tracks_at = msg, now
        self.engine = msg.get("engine", self.engine)
        if self.counters:
            name = self.cfg.id
            self.counters.add(f"{name}.detections")
            self.counters.add(f"{name}.skipped_frames", msg.get("skipped", 0))
            self.counters.observe(f"{name}.detect_ms", sum(msg["timing"].values()) / max(1, msg.get("batch", 1)))
            exact = msg.get("pipeline_ms")  # measured with perf_counter: t_done - t_frame ticks every 15.6 ms on Windows
            self.counters.observe(f"{name}.pipeline_ms", exact if exact is not None else (msg["t_done"] - msg["t_frame"]) * 1000)
            self.counters.set(f"{name}.people", len(msg["tracks"]))
        captured = msg["t_frame"] - self.cfg.video_delay
        pose = self.link.pose_at(captured)
        if pose is None:
            self.people, self.zones = [], {}
            return
        slot = self.slot
        self.people = rules.place(msg, pose, self.lens)
        self.zones = {p.id: zone for p in self.people if (zone := rules.zone_of(slot, p))}
        if self.target_id is not None:
            self._follow(msg, pose, captured, now)
        looking = (self.tracking and not self.paused and (self.target_id is None or self.lost_since is not None)
                   and not self._holding_back())
        if self.cue and self._answer_cue(slot, now):
            looking = False
        if looking and slot.registered_faces:
            # Canon: only registered faces (switched on) are picked, the highest priority first, at once
            self.selector.reset()
            pick = self._registered_pick(slot)
            if pick is not None and (not self._is_target(pick[0]) or pick[1] != self.target_face):
                self.select_id(pick[0], f"registered face: {self.face_names.get(pick[1], pick[1])}")
        elif looking and slot.auto_select.enabled:
            pick = self.selector.update(slot, self.people, now)
            if pick is not None and not self._is_target(pick):
                self.select_id(pick, "auto select")
        else:
            self.selector.reset()

    def audio_cue(self, channel: int, label: str, preset: int | None, face: str, slow: bool) -> None:
        """A microphone started speaking (audio-follow): go to its person's place and track them.

        With a preset the camera goes there first (unless it is live: then only a visible person
        is taken over, with the calm on-air motion); then the person with the mic's registered
        face, or else the person nearest the display position, becomes the target.
        """
        now = time.monotonic()
        self.cue = (face, label, now + CUE_TIMEOUT, channel)
        self._event("audio", f"{label or f'mic {channel}'} is speaking: framing them")
        if preset and not slow:
            self.select_id(None, "audio cue")
            self.set_tracking(False, "audio cue: moving to the preset")  # (first: off drops a pending start)
            self.move = Move(f"preset {preset} for {label or f'mic {channel}'}", None, now, now + 20.0, then_track=True)
            self._spawn(self.link.recall_preset(preset - 1), f"preset {preset} recall")
            self.cue = (face, label, now + CUE_TIMEOUT + 20.0, channel)  # plus the move
        elif not self.tracking:
            self.set_tracking(True, "audio cue", start_at_initial=False)

    def _answer_cue(self, slot: AppSettings, now: float) -> bool:
        """Find the cued person; True when the cue decided the target (or still waits for a move)."""
        face, label, deadline, channel = self.cue
        if now > deadline:
            self.cue = None
            self._event("audio", f"{label or f'mic {channel}'}: nobody to frame", level="warning")
            return False
        if self.move is not None or not self.tracking:
            return True  # still going to the preset
        candidates = [p for p in self.people if not self.zones.get(p.id)]
        if face:
            candidates = [p for p in candidates if (p.track.get("face") or {}).get("id") == face]
        if not candidates:
            return True
        pose = self.link.pose_now()
        aim = (pose.pan if pose else 0.0, pose.tilt if pose else 0.0)
        pick = min(candidates, key=lambda p: (p.pan - aim[0]) ** 2 + (p.tilt - aim[1]) ** 2)
        self.cue = None
        if not self._is_target(pick.id):
            self.select_id(pick.id, f"audio: {label or f'mic {channel}'}")
        self.framing_mic = channel
        return True

    def _registered_pick(self, slot: AppSettings) -> tuple[int, str] | None:
        """The present person with the highest-priority registered face (track id, face id)."""
        order = [ref.id for ref in self.cfg.faces if ref.enabled]
        found = [(order.index(face), p.id, face) for p in self.people
                 if (face := (p.track.get("face") or {}).get("id")) in order and rules.selectable(slot, p)]
        return (min(found)[1], min(found)[2]) if found else None

    def _is_target(self, track_id: int) -> bool:
        """Is this track the target? (Not by the number alone after a vision worker restart.)"""
        return track_id == self.target_id and not self.stale_id

    def _follow(self, msg: dict, pose: Pose, captured: float, now: float) -> None:
        track = next((t for t in msg["tracks"] if self._is_target(t["id"])), None)
        face = (track.get("face") or {}).get("id") if track else None
        if self.target_face and face and face != self.target_face:
            track = None  # identity lock: the target's track shows someone else now (the tracker swapped IDs)
        if track is None:
            track = self._reacquire(msg, pose, captured)
        if track is None or track["missed"]:
            return
        person = next((p for p in self.people if p.id == track["id"]), None)
        if person is None:
            return
        slot = self.slot
        halting = rules.halting_area_of(slot, person)
        if halting is not None:
            self._release(now, f"person {person.id} entered tracking halting area {halting + 1}: tracking stopped")
            return
        self._check_fixed(person, slot)
        self.head.update(captured, person.pan, person.tilt)
        face = (track.get("face") or {}).get("id")
        if face and self.target_face is None:
            self.target_face = face
            self._event("target", f"target recognised: {self.face_names.get(face, face)}")
        shot = self.shot = self.framer.shot(slot, person, self.people, self.zones, msg, pose, self.lens, captured)
        self.controller.observe(Observation(captured, shot.x, shot.y, track["box"][3] - track["box"][1],
                                            msg["width"], msg["height"], anchor=shot.anchor, offset=shot.offset,
                                            want_hfov=shot.want_hfov, target_height=shot.target_height,
                                            zoom_step=shot.zoom_step,
                                            cut_bottom=cut_at_bottom(track["box"], msg["height"])), pose)
        self.last_seen, self.last_seen_captured = now, captured
        if self.lost_since is not None:
            self._event("target", f"target found again: person {self.target_id}")
            self.lost_since, self.went_home = None, False

    def _check_fixed(self, person: rules.Placed, slot: AppSettings) -> None:
        """Fixed viewing angle areas: hold the area's shot while the target is inside."""
        if self.fixed is not None:
            if slot.fixed_areas[self.fixed].contains(person.pan, person.tilt, FIXED_EXIT_MARGIN):
                return
            self._event("target", f"target left fixed viewing angle area {self.fixed + 1}: tracking")
            self.fixed = None
            self.controller.hold()
            return
        if self.paused or self._manual_active:
            return  # the operator has the camera: the area takes the shot once tracking is back
        i = rules.fixed_area_of(slot, person)
        if i is not None:
            self.fixed = i
            self._event("target", f"target in fixed viewing angle area {i + 1}: angle fixed")
            if self.tracking:
                self.go_to(rules.view_for_area(slot.fixed_areas[i], self.lens), f"fixed viewing angle area {i + 1}")

    def _reacquire(self, msg: dict, pose: Pose, captured: float) -> dict | None:
        """The target's ID vanished (missed detections, hidden, walked out of the picture):
        adopt a track that appeared since then, near where the person was last seen.

        The search circle starts at 3 degrees and grows with the time missing (people keep
        walking). With two similar candidates nobody is picked: following the wrong person
        is worse than holding the shot. People in halting or exclusion areas are never
        adopted. A target with a recognised registered face is found again by that face
        anywhere in the picture, and nobody recognised as someone else is adopted.
        """
        if self.target_face:  # identity lock: the same registered face, anywhere in the picture
            same = next((p for p in self.people if (p.track.get("face") or {}).get("id") == self.target_face
                         and not self.zones.get(p.id)), None)
            if same:
                self.counts["refinds"] += 1
                self._event("target", f"target re-found by face as person {same.id} (was {self.target_id})")
                self.target_id, self.stale_id = same.id, False
                return same.track
        kalman = self.head
        since = time.monotonic() - self.last_seen
        if not kalman.ready or since > self.cfg.refind_window:
            return None
        # last known direction, extrapolated at most half a second (people stop and turn)
        pred_pan, pred_tilt, _, _ = kalman.predict(min(captured, self.last_seen_captured + 0.5))
        radius = min(3.0 + 6.0 * since, 0.5 * self.lens.hfov(pose.zoom) + 3.0)
        candidates = []
        for p in self.people:  # only tracks new since the loss, and nobody known to be someone else
            other_face = (p.track.get("face") or {}).get("id")
            if p.track["age"] > since + 1.0 or self.zones.get(p.id) or (self.target_face and other_face
                                                                        and other_face != self.target_face):
                continue
            d = ((p.pan - pred_pan) ** 2 + (p.tilt - pred_tilt) ** 2) ** 0.5
            if d < radius:
                candidates.append((d, p.track))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        if len(candidates) > 1 and candidates[1][0] < candidates[0][0] * 1.5 + 1.0:
            return None  # ambiguous
        best = candidates[0][1]
        self.counts["refinds"] += 1
        self._event("target", f"target re-found as person {best['id']} (was {self.target_id})")
        self.target_id, self.stale_id = best["id"], False
        return best

    # --- control -------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        """Called ~50 times a second by the app."""
        goal = 1.0 if self.on_air else 0.0
        if self.air != goal:
            step = 0.02 / AIR_BLEND_S
            self.air = min(goal, self.air + step) if goal > self.air else max(goal, self.air - step)
            self._apply_air()
        if now < self.manual_until:
            return
        if self._manual_active:  # manual control just ended: never leave the camera moving
            self._manual_active = False
            self._send(0, 0, 0)
            self.controller.hold()
            self.quiet_until = now + QUIET_AFTER_MOVE
        pose = self.link.pose_now()
        self._check_move(now, pose)
        self._check_manual(now)
        if not self.tracking or self.paused:
            return
        if self.no_video or pose is None:
            self._send(0, 0, 0)
            self.controller.hold()  # stopped: start again from standstill, not at the old speed
            return
        if self.target_id is not None and self.lost_since is None and now - self.last_seen > self.cfg.lost_after:
            self.lost_since, self.fixed = now, None
            self.counts["targets_lost"] += 1
            self._event("target", f"target lost: person {self.target_id}", level="warning")
        if self.lost_since is not None:
            self._while_lost(now)
        if self.target_id is None or self.lost_since is not None or self.fixed is not None:
            self._send(0, 0, 0)  # no-op during our own absolute moves (nothing new to send)
            return
        cmd = self.controller.command(now, Pose(now, pose.pan, pose.tilt, pose.zoom))
        self.debug = cmd.debug
        if self.move and (cmd.pan or cmd.tilt or cmd.zoom):
            self.move = None  # following the target takes over from the absolute move
            self.quiet_until = now + QUIET_AFTER_MOVE
        if not self.move:
            self._send(cmd.pan, cmd.tilt, cmd.zoom)

    def _while_lost(self, now: float) -> None:
        slot = self.slot
        if (slot.lost_action == "return" and slot.initial.set and not self.went_home
                and now - self.lost_since >= slot.return_after and not self._holding_back()):
            self.went_home = True
            self.go_to(slot.initial, "initial position")
        if self.target_id is not None and slot.auto_select.enabled and now - self.last_seen > self.cfg.refind_window:
            self._event("target", f"stopped looking for person {self.target_id}: auto select picks the next")
            self.target_id = None
            self._forget_person()

    def _release(self, now: float, message: str) -> None:
        """Let go of the target (it entered a tracking halting area); the lost behaviour applies."""
        self._event("target", message)
        self.target_id, self.fixed = None, None
        self._forget_person()
        self.lost_since, self.went_home = now, False

    def _check_move(self, now: float, pose: Pose | None) -> None:
        m = self.move
        if m is None or pose is None:
            return
        if m.target is not None:
            arrived = (abs(pose.pan - m.target.pan) < 0.3 and abs(pose.tilt - m.target.tilt) < 0.3
                       and abs(pose.zoom - m.target.zoom) < 0.01 * self.lens.zoom_max)
        else:
            arrived = now - m.started > 0.5 and not self._camera_moving(now)
        if not arrived and now < m.deadline:
            return
        if not arrived:
            self._event("camera", f"{m.reason}: the camera didn't get there in time", level="warning")
        self.move = None
        self.quiet_until = now + QUIET_AFTER_MOVE
        if m.then_track:
            self.set_tracking(True, f"{m.reason} reached (Camera Preset Link: On)", start_at_initial=False)

    def _check_manual(self, now: float) -> None:
        """Notice someone else moving the camera (vMix, a joystick, BirdUI): a manual operation."""
        if (self.move or self._manual_active or self.link.commanding or now < self.quiet_until
                or now - self.link.last_motion < QUIET_AFTER_MOVE):
            return
        moved = self._camera_moving(now)
        if moved:
            self.still_since = None
            self.manual_at = now  # moved by hand (with tracking on or off): audio-follow leaves it alone a while
            if self.tracking and not self.paused:
                self._manual_operation(f"camera moved by another controller ({moved})")
        elif self.paused:
            if self.still_since is None:
                self.still_since = now
            elif now - self.still_since >= STILL_BEFORE_RESUME:
                self.paused = False
                self.controller.hold()
                self._event("tracking", "manual operation over: tracking again")

    def _camera_moving(self, now: float, window: float = 0.25) -> str:
        """How the camera moved in the last `window` seconds ("" = it didn't)."""
        a, b = self.link.pose_at(now - window), self.link.pose_now()
        if a is None or b is None:
            return ""
        moves = []
        if abs(b.pan - a.pan) > 0.15:
            moves.append(f"pan {b.pan - a.pan:+.1f}°")
        if abs(b.tilt - a.tilt) > 0.15:
            moves.append(f"tilt {b.tilt - a.tilt:+.1f}°")
        if abs(b.zoom - a.zoom) > 0.004 * self.lens.zoom_max:
            moves.append(f"zoom {b.zoom - a.zoom:+.0f}")
        return ", ".join(moves)

    def _send(self, pan: int, tilt: int, zoom: int) -> None:
        self.last_command = (pan, tilt, zoom)
        self.link.request(pan, tilt, zoom)

    def video_stopped(self) -> None:
        """Watchdog: no detections are arriving."""
        if not self.no_video:
            self.counts["watchdog_stops"] += 1
            self.no_video = True
            self.controller.hold()  # the camera is stopped: motion starts again from standstill
            self._event("video", "no detections arriving: camera stopped", level="error")

    def video_resumed(self) -> None:
        if self.no_video:
            self.no_video = False
            self._event("video", "detections arriving again")

    def worker_restarted(self) -> None:
        """A new vision worker started: its track ids start at 1 again, so the old ids (the target's,
        and everything kept per person) mean nobody in particular now. The target is found again like
        a lost one: by its registered face anywhere, or as a new track where it was last seen. The
        camera itself is left as it is."""
        if self.target_id is not None:
            self.stale_id = True
        self.latest, self.people, self.zones = None, [], {}
        self.selector.reset()
        self.framer.reset()
        self.shot = None

    def reset_counts(self) -> None:
        self.counts = dict.fromkeys(self.counts, 0)

    def _event(self, kind: str, message: str, level: str = "info") -> None:
        if self.log:
            self.log.event(kind, message, level=level, source=self.cfg.id)

    # --- status ----------------------------------------------------------------------------

    def areas_in_view(self, pose: Pose | None) -> list[dict]:
        """The active slot's areas, projected into the current picture (for drawing)."""
        if pose is None:
            return []
        slot, out = self.slot, []

        def add(kind: str, index: int, area: Area) -> None:
            rect = area_to_view(area, pose, self.lens)
            if rect:
                out.append({"kind": kind, "index": index, "rect": [round(v, 4) for v in rect]})

        r = slot.shooting_range
        if r.enabled:
            add("range", 0, Area(enabled=True, left=r.left, right=r.right, top=r.top, bottom=r.bottom))
        if slot.initial.set:
            add("initial", 0, view_area(slot.initial, self.lens))
        if slot.priority_area.enabled:
            add("priority", 0, slot.priority_area)
        if slot.auto_select.enabled and slot.auto_select.area.enabled:
            add("auto_select", 0, slot.auto_select.area)
        for kind, areas in (("fixed", slot.fixed_areas), ("exclusion", slot.exclusion_areas),
                            ("halting", slot.halting_areas)):
            for i, a in enumerate(areas):
                if a.enabled:
                    add(kind, i, a)
        return out

    def status(self) -> dict:
        pose = self.link.pose_now()
        msg = self.latest or {}
        slot = self.slot
        return {
            "id": self.cfg.id, "name": self.cfg.name, "state": self.state, "tracking": self.tracking,
            "target": self.target_id, "engine": self.engine,
            "frame": {"width": msg.get("width", 0), "height": msg.get("height", 0)},
            "tracks": msg.get("tracks", []),
            "pose": None if pose is None else {"pan": round(pose.pan, 2), "tilt": round(pose.tilt, 2),
                                               "zoom": round(pose.zoom), "hfov": round(self.lens.hfov(pose.zoom), 2)},
            "command": self.last_command, "debug": self.debug,
            "camera_ok": self.link.visca_ok, "freed_age": round(min(self.link.freed_age, 999), 2),
            "source": self.source_stats, "framing": dataclasses.asdict(slot.composition),
            "halting_area": slot.halting_area, "counts": self.counts,
            "slot": self.cfg.active_slot, "slot_names": [s.name for s in self.cfg.slots],
            "settings_rev": self.settings_rev, "paused": self.paused,
            "moving": self.move.reason if self.move else "", "fixed": self.fixed, "zones": self.zones,
            "auto_select_wait": {pid: round(wait, 1) for pid, wait in self.selector.progress(slot, time.monotonic()).items()},
            "areas": self.areas_in_view(pose), "problems": slot.problems(self.lens),
            "group": self.shot.group if self.shot and self.target_id is not None else [],
            "facing": self.shot.facing if self.shot and self.target_id is not None else "",
            "posture": self.shot.posture if self.shot and self.target_id is not None else "",
            "target_face": self.target_face,
            "faces": [ref.model_dump() for ref in self.cfg.faces],
            "on_air": self.on_air, "on_air_tally": self.on_air_tally, "on_air_manual": self.on_air_manual,
            "air": round(self.air, 2), "audio_follow": self.audio_follow,
            "cue": {"label": self.cue[1], "channel": self.cue[3]} if self.cue else None,
        }

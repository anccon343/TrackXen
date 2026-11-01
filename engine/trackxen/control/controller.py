"""Turns "where the person is" into camera speed commands.

Per update:
1. The person's head point in a frame + the camera's position when that frame was
   captured -> the person's direction in room angles (Kalman filtered).
2. Predict that direction `lookahead` seconds ahead (video and command delays).
3. The camera should point so the head sits at the framing point (Canon's Display Position).
4. Inside the dead zone (Canon's Pan/Tilt Halting Area) the camera stays still; once it
   moves, it keeps going until the error is small again (no stop-start jitter).
5. Speed = the person's own speed (feed-forward) + gain x error, limited by the motion
   profile's top speed and acceleration, then mapped to the camera's real speed steps.
6. Auto zoom keeps the person at the wanted size (Canon's Display Size).

The session can also limit where the camera may aim (`constrain`: shooting range, priority
display area), how far it may zoom in (`max_zoom`) and whether it may tilt (`fix_tilt`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from trackxen.control.camera import Pose
from trackxen.control.kalman import AngleKalman
from trackxen.control.lens import Lens

# Degrees per second for VISCA speed steps 1..24: a Sony-style default until each camera
# is measured with tools/visca_probe.py sweep (the simulator uses this same table).
DEFAULT_PAN_SPEEDS = [0.4, 0.8, 1.3, 2.0, 2.9, 4.0, 5.4, 7.0, 9.0, 11.5, 14.4, 18.0,
                      22.0, 27.0, 33.0, 40.0, 48.0, 57.0, 67.0, 78.0, 90.0, 103.0, 117.0, 130.0]
DEFAULT_TILT_SPEEDS = DEFAULT_PAN_SPEEDS[:23]


@dataclass
class SpeedTable:
    pan: list[float] = field(default_factory=lambda: list(DEFAULT_PAN_SPEEDS))
    tilt: list[float] = field(default_factory=lambda: list(DEFAULT_TILT_SPEEDS))

    @staticmethod
    def step_for(table: list[float], dps: float) -> int:
        """Signed speed step closest to `dps` (on a log scale); 0 when slower than half of step 1."""
        speed = abs(dps)
        if speed < table[0] * 0.5:
            return 0
        best = min(range(len(table)), key=lambda i: abs(math.log(table[i] / speed)))
        return (best + 1) * (1 if dps > 0 else -1)

    @staticmethod
    def speed_of(table: list[float], step: int) -> float:
        return 0.0 if step == 0 else math.copysign(table[min(abs(step), len(table)) - 1], step)


@dataclass
class Framing:
    anchor_x: float = 0.5  # where the head should be, as a fraction of the frame width
    anchor_y: float = 0.30  # ... and height (0 = top)
    dead_zone_x: float = 0.06  # half-size of the "don't move" zone, fraction of the frame
    dead_zone_y: float = 0.08
    display_size: bool = False  # zoom to target_height when a target is locked (Canon Display Size)...
    auto_zoom: bool = False  # ...and keep it there while tracking (needs display_size)
    target_height: float = 0.55  # person height as a fraction of the frame
    zoom_dead_band: float = 0.15  # relative size error tolerated before zooming


@dataclass
class Motion:
    """A motion profile. The tracker switches to a calmer one while a camera is on air."""

    max_pan: float = 30.0  # deg/s at a 40-degree field of view (scaled down when zoomed in)
    max_tilt: float = 15.0
    max_accel: float = 45.0  # deg/s^2
    gain: float = 1.6  # deg/s of correction per degree of error
    feedforward: float = 0.9  # share of the person's own speed added to the command
    settle_ratio: float = 0.3  # keep moving until the error is below dead zone x this
    lookahead: float = 0.12  # seconds to predict ahead
    max_zoom_step: int = 3  # 1..8 (VISCA zoom speeds 0..7)


@dataclass
class Observation:
    t: float  # when the frame was captured (monotonic)
    x: float  # head point, frame pixels
    y: float
    height: float  # person box height in pixels
    width: int  # frame size
    frame_height: int
    # Auto Framing (rules/framing.py) may override the composition for this sighting:
    anchor: tuple[float, float] | None = None  # where (x, y) should sit in the picture, 0..1
    offset: tuple[float, float] = (0.0, 0.0)  # degrees added to the aim (e.g. towards a group's middle)
    want_hfov: float | None = None  # zoom to this field of view (a group)...
    target_height: float | None = None  # ...or bring the person to this height share (Sit/Stand)
    zoom_step: int | None = None  # top zoom speed step for this framing


@dataclass
class Command:
    pan: int = 0  # signed VISCA speed steps
    tilt: int = 0
    zoom: int = 0
    debug: dict = field(default_factory=dict)


class Controller:
    def __init__(self, lens: Lens | None = None, speeds: SpeedTable | None = None,
                 framing: Framing | None = None, motion: Motion | None = None):
        self.lens = lens or Lens()
        self.speeds = speeds or SpeedTable()
        self.framing = framing or Framing()
        self.motion = motion or Motion()
        self.kalman = AngleKalman()
        # (pan, tilt, hfov, vfov) -> the allowed aim; None = anywhere
        self.constrain: Callable[[float, float, float, float], tuple[float, float]] | None = None
        self.max_zoom: float | None = None  # raw zoom limit (priority display area must fit)
        self.fix_tilt = False
        self.last_obs: Observation | None = None
        self._moving = [False, False]
        self._velocity = [0.0, 0.0]
        self._last_t: float | None = None
        self._sizing = True  # zooming to the display size once after a target is locked

    def reset(self) -> None:
        """Forget the person (new target)."""
        self.kalman.reset()
        self.last_obs = None
        self._sizing = True
        self.hold()

    def hold(self) -> None:
        """Forget the camera's motion (it was stopped or moved by something else), keep the person."""
        self._moving = [False, False]
        self._velocity = [0.0, 0.0]
        self._last_t = None

    def observe(self, obs: Observation, pose: Pose) -> tuple[float, float]:
        """Add a sighting; `pose` is where the camera pointed when the frame was captured."""
        dpan, dtilt = self.lens.pixel_to_angles(obs.x, obs.y, obs.width, obs.frame_height, pose.zoom)
        pan, tilt = pose.pan + dpan, pose.tilt + dtilt
        self.kalman.update(obs.t, pan, tilt)
        self.last_obs = obs
        return pan, tilt

    def command(self, now: float, pose: Pose, hold_after: float = 0.6) -> Command:
        """Speed steps for now. Stops if there has been no sighting for `hold_after` seconds."""
        dt = 0.02 if self._last_t is None else min(0.1, max(0.0, now - self._last_t))
        self._last_t = now
        obs = self.last_obs
        if not self.kalman.ready or obs is None or now - obs.t > hold_after:
            self._moving = [False, False]
            self._velocity = [0.0, 0.0]
            return Command(debug={"state": "hold"})

        m, f = self.motion, self.framing
        tp, tt, vp, vt = self.kalman.predict(now + m.lookahead)
        ax, ay = obs.anchor or (f.anchor_x, f.anchor_y)
        off_pan, off_tilt = self.lens.pixel_to_angles(ax * obs.width, ay * obs.frame_height,
                                                      obs.width, obs.frame_height, pose.zoom)
        hfov, vfov = self.lens.hfov(pose.zoom), self.lens.vfov(pose.zoom)
        aim = (tp - off_pan + obs.offset[0], tt - off_tilt + obs.offset[1])
        allowed = self.constrain(*aim, hfov, vfov) if self.constrain else aim
        held = [abs(allowed[i] - aim[i]) > 0.01 for i in (0, 1)]  # pinned at a limit: no feed-forward
        error = [allowed[0] - pose.pan, allowed[1] - pose.tilt]
        error[0] = (error[0] + 180) % 360 - 180
        dead = [f.dead_zone_x * hfov, f.dead_zone_y * vfov]
        zoom_scale = min(1.0, max(0.08, hfov / 40.0))  # slower top speed when zoomed in
        limits = [m.max_pan * zoom_scale, m.max_tilt * zoom_scale]
        target_speed = [vp, vt]
        steps = [0, 0]
        for axis, table in ((0, self.speeds.pan), (1, self.speeds.tilt)):
            if axis == 1 and self.fix_tilt:
                self._moving[1], self._velocity[1] = False, 0.0
                continue
            err = error[axis]
            if not self._moving[axis] and abs(err) > dead[axis]:
                self._moving[axis] = True
            elif self._moving[axis] and abs(err) < dead[axis] * m.settle_ratio:
                self._moving[axis] = False
            feedforward = 0.0 if held[axis] else m.feedforward * target_speed[axis]
            wanted = feedforward + m.gain * err if self._moving[axis] else 0.0
            wanted = max(-limits[axis], min(limits[axis], wanted))
            if dt > 0:  # acceleration limit: smooth starts and stops
                step = m.max_accel * dt
                wanted = max(self._velocity[axis] - step, min(self._velocity[axis] + step, wanted))
            self._velocity[axis] = wanted
            steps[axis] = SpeedTable.step_for(table, wanted)

        zoom = self._zoom(obs, pose, tt, vfov)
        return Command(steps[0], steps[1], zoom, debug={
            "state": "moving" if any(self._moving) else "holding", "error": [round(e, 3) for e in error],
            "dead_zone": [round(d, 3) for d in dead], "velocity": [round(v, 3) for v in self._velocity],
            "target_speed": [round(vp, 3), round(vt, 3)], "limited": held})

    def _zoom(self, obs: Observation, pose: Pose, head_tilt: float, vfov: float) -> int:
        """Signed zoom speed step towards the wanted size: the display size, or what Auto Framing asks."""
        f, m = self.framing, self.motion
        if obs.want_hfov:  # ratio > 1: zoom in
            ratio = self.lens.hfov(pose.zoom) / obs.want_hfov
        elif obs.height <= 0:
            return 0
        elif obs.target_height:
            ratio = obs.target_height / (obs.height / obs.frame_height)
        elif f.display_size and (f.auto_zoom or self._sizing):
            ratio = f.target_height / (obs.height / obs.frame_height)
        else:
            return 0
        if abs(math.log(ratio)) <= math.log(1 + f.zoom_dead_band):
            self._sizing = False  # reached once; only Auto Zoom keeps adjusting
            return 0
        top = m.max_zoom_step if obs.zoom_step is None else min(obs.zoom_step, m.max_zoom_step)
        if any(self._moving) or top <= 0:  # (0: no zooming, e.g. while on air)
            return 0
        size = min(top, max(1, round(abs(math.log(ratio)) * 6)))
        zoom = size if ratio > 1 else -size
        limit = self.lens.zoom_max * 0.99 if self.max_zoom is None else min(self.max_zoom, self.lens.zoom_max * 0.99)
        if zoom > 0 and (pose.zoom >= limit or (self.fix_tilt and abs(head_tilt - pose.tilt) > 0.35 * vfov)):
            return 0  # at the limit, or (tilt fixed) zooming in would push the person out of the picture
        if zoom < 0 and pose.zoom <= 1:
            return 0
        return zoom


def head_point(box: list[float], keypoints: list[list[float]], min_conf: float = 0.4) -> tuple[float, float]:
    """The point to frame: middle of the visible face keypoints, else near the top of the box."""
    face = [(x, y) for x, y, c in keypoints[:5] if c >= min_conf]  # nose, eyes, ears
    if face:
        return sum(p[0] for p in face) / len(face), sum(p[1] for p in face) / len(face)
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, y1 + 0.1 * (y2 - y1)

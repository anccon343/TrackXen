"""Turns "where the person is" into camera speed commands.

Per update:
1. The person's head point in a frame + the camera's position when that frame was
   captured -> the person's direction in room angles (Kalman filtered).
2. Predict that direction `lookahead` seconds ahead (video and command delays).
3. The camera should point so the head sits at the framing point (Canon's Display Position).
4. Inside the dead zone (Canon's Pan/Tilt Halting Area) the camera stays still; once it
   moves, it keeps going until the error is small again and the person has (nearly) stopped:
   a walking person is followed in one movement, not in stop-start pulses. A move doesn't turn
   back inside the dead zone (a walker who stops is passed a little: it ends there). The dead
   zone is never smaller than what the camera can hold (a walking head bobs; the slowest speed
   step coasts during the video and command delays).
5. Speed = the person's own speed (feed-forward, low-passed: the Kalman speed is noisy) +
   gain x error, limited by the motion profile's top speed (but never below what keeps up
   with the person) and acceleration (optionally an S-curve), then mapped to the camera's
   real speed steps, with hysteresis between neighbouring steps. Ahead of a walking person
   (after catching up) the camera waits for them rather than turning back.
6. Auto zoom keeps the person at the wanted size (Canon's Display Size), also while a walker is
   followed; not while the camera catches up.

The session can also limit where the camera may aim (`constrain`: shooting range, priority
display area), how far it may zoom in (`max_zoom`) and whether it may tilt (`fix_tilt`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

from annotated_types import Interval

from trackxen.control.camera import Pose
from trackxen.control.kalman import AngleKalman
from trackxen.control.lens import Lens

# Degrees per second for VISCA speed steps 1..24: a Sony-style default until each camera
# is measured with tools/visca_probe.py sweep (the simulator uses this same table).
DEFAULT_PAN_SPEEDS = [0.4, 0.8, 1.3, 2.0, 2.9, 4.0, 5.4, 7.0, 9.0, 11.5, 14.4, 18.0,
                      22.0, 27.0, 33.0, 40.0, 48.0, 57.0, 67.0, 78.0, 90.0, 103.0, 117.0, 130.0]
DEFAULT_TILT_SPEEDS = DEFAULT_PAN_SPEEDS[:23]

# A move does not end while the person moves faster than SETTLE_SPEED x the dead zone per second, nor faster
# than SETTLE_SPEED_MIN deg/s (above the Kalman's speed noise): otherwise the camera catches up, stops while
# the person walks on, and starts again once the error has grown past the dead zone (17-57 stop-starts a
# minute while walking, measured). A standing person still settles.
SETTLE_SPEED = 0.15
SETTLE_SPEED_MIN = 0.3
# The dead zone is at least MIN_DEAD_ZONE degrees (a walking head bobs about +-0.08 degrees at 15 m), and at
# least the distance the slowest speed step covers in SLOWEST_STEP_COAST s (it keeps going for the video and
# command delays): smaller, and the camera hunts to and fro (Halting Area off, tight shots).
MIN_DEAD_ZONE = 0.15
SLOWEST_STEP_COAST = 0.27
# The zoomed-in top speed is never below the person's own speed x KEEP_UP (up to the unzoomed top speed): a
# brisk walker at a tight shot would otherwise walk out of the picture.
KEEP_UP = 2.0


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
    # Smoothness (each App Settings slot's Pan/Tilt Smoothness scales these: tracking_settings.smoothness_motion).
    # The ranges are checked where a profile is read from the settings (CameraConfig.motion).
    # s: low-pass on the person's speed used for feed-forward (0 = the raw Kalman speed)
    ff_tau: Annotated[float, Interval(ge=0.0, le=2.0)] = 0.15
    # s to build up to the full acceleration: an S-curve (0 = constant acceleration)
    jerk_time: Annotated[float, Interval(ge=0.0, le=2.0)] = 0.0
    # share of the gap between neighbouring speed steps a change must pass (0 = the nearest step)
    step_hysteresis: Annotated[float, Interval(ge=0.0, le=0.5)] = 0.25


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
    cut_bottom: bool = False  # the person's box ends at the picture's bottom edge (bigger than `height` says)


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
        self._accel = [0.0, 0.0]  # deg/s^2 (S-curve)
        self._step = [0, 0]  # the speed steps last commanded (step hysteresis)
        self._dir = [0, 0]  # the way this move has turned the camera (it doesn't turn back inside the dead zone)
        self._target_speed = [0.0, 0.0]  # the person's speed, low-passed (feed-forward)
        self._last_t: float | None = None
        self._sizing = True  # zooming to the display size once after a target is locked

    def reset(self) -> None:
        """Forget the person (new target)."""
        self.kalman.reset()
        self.last_obs = None
        self._sizing = True
        self._target_speed = [0.0, 0.0]
        self.hold()

    def hold(self) -> None:
        """Forget the camera's motion (it was stopped or moved by something else), keep the person."""
        self._moving = [False, False]
        self._velocity = [0.0, 0.0]
        self._accel = [0.0, 0.0]
        self._step = [0, 0]
        self._dir = [0, 0]
        self._target_speed = [0.0, 0.0]  # (filter state: a speed from before the pause would drive the wrong way)
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
            self._velocity, self._accel, self._step = [0.0, 0.0], [0.0, 0.0], [0, 0]
            return Command(debug={"state": "hold"})

        m, f = self.motion, self.framing
        tp, tt, vp, vt = self.kalman.predict(now + m.lookahead)
        k = 1.0 if m.ff_tau <= 0 else min(1.0, dt / m.ff_tau)  # (no time passed: no change)
        target_speed = self._target_speed = [s + (v - s) * k for s, v in zip(self._target_speed, (vp, vt))]
        ax, ay = obs.anchor or (f.anchor_x, f.anchor_y)
        off_pan, off_tilt = self.lens.pixel_to_angles(ax * obs.width, ay * obs.frame_height,
                                                      obs.width, obs.frame_height, pose.zoom)
        hfov, vfov = self.lens.hfov(pose.zoom), self.lens.vfov(pose.zoom)
        aim = (tp - off_pan + obs.offset[0], tt - off_tilt + obs.offset[1])
        allowed = self.constrain(*aim, hfov, vfov) if self.constrain else aim
        held = [abs(allowed[i] - aim[i]) > 0.01 for i in (0, 1)]  # pinned at a limit: no feed-forward
        error = [allowed[0] - pose.pan, allowed[1] - pose.tilt]
        error[0] = (error[0] + 180) % 360 - 180
        # pinned at a limit and coasted past it (the person is beyond it): come back inside, don't rest out there
        past = [held[i] and error[i] * (aim[i] - allowed[i]) < 0 and abs(error[i]) > 0.02 for i in (0, 1)]
        tables = (self.speeds.pan, self.speeds.tilt)
        dead = [max(f.dead_zone_x * hfov, MIN_DEAD_ZONE, tables[0][0] * SLOWEST_STEP_COAST),
                max(f.dead_zone_y * vfov, MIN_DEAD_ZONE, tables[1][0] * SLOWEST_STEP_COAST)]
        zoom_scale = min(1.0, max(0.08, hfov / 40.0))  # slower top speed when zoomed in...
        top = (m.max_pan, m.max_tilt)  # ...but never too slow to keep up with the person
        limits = [min(top[i], max(top[i] * zoom_scale, KEEP_UP * abs(target_speed[i]))) for i in (0, 1)]
        steps = [0, 0]
        following = [False, False]  # moving only to follow a walker inside the dead zone (doesn't block the zoom)
        for axis, table in enumerate(tables):
            if axis == 1 and self.fix_tilt:
                self._moving[1], self._velocity[1], self._accel[1], self._step[1] = False, 0.0, 0.0, 0
                continue
            err = error[axis]
            slow = max(SETTLE_SPEED * dead[axis], SETTLE_SPEED_MIN)
            walking = abs(target_speed[axis]) > slow
            following[axis] = walking and abs(err) <= dead[axis]
            if not self._moving[axis] and (abs(err) > dead[axis] or past[axis]):
                self._moving[axis], self._dir[axis] = True, 0
            elif self._moving[axis] and abs(err) < dead[axis] * m.settle_ratio and not walking and not past[axis]:
                self._moving[axis] = False
            feedforward = 0.0 if held[axis] else m.feedforward * target_speed[axis]
            wanted = feedforward + m.gain * err if self._moving[axis] else 0.0
            # ahead of a walking person (caught up late and overshot): wait for them, don't turn back against the walk
            # (still walking now, too: when the person stops, the low-passed speed takes a while to drop)
            ahead = (walking and not held[axis] and err * target_speed[axis] < 0
                     and (vp, vt)[axis] * target_speed[axis] > slow * abs(target_speed[axis]))
            if ahead and wanted * target_speed[axis] < 0:
                wanted = 0.0
            if self._moving[axis] and wanted * self._dir[axis] < 0 and abs(err) <= dead[axis] and not past[axis]:
                # eased to a stop a little past the person (a walker who stopped; the delays): the move ends there, it
                # doesn't turn back inside the dead zone (a reversal at every stop is a jerk); past it, it does
                self._moving[axis], wanted = False, 0.0
            wanted = max(-limits[axis], min(limits[axis], wanted))
            wanted = self._velocity[axis] = self._ramp(axis, wanted, m.max_accel, dt)
            if wanted:
                self._dir[axis] = 1 if wanted > 0 else -1
            step = SpeedTable.step_for(table, wanted)
            if self._moving[axis] and step == 0:  # slower than the camera's slowest step
                # Settle inside the dead zone (it would get no closer), and also within the distance the
                # slowest step coasts during the video and command delays: closer than that, step 1 would
                # overshoot a tiny dead zone (Halting Area off, tight shot) and hunt to and fro. Not while waiting
                # for a walking person: the move goes on when they catch up, not a whole dead zone later.
                if abs(err) <= max(dead[axis], table[0] * m.lookahead) and not past[axis]:
                    self._moving[axis] = ahead
                else:  # outside it: the slowest step, or the camera would never get there
                    step = 1 if (wanted if wanted else err) > 0 else -1
            steps[axis] = self._step[axis] = _hysteresis(table, self._step[axis], step, wanted, m.step_hysteresis)

        # an axis catching up blocks the zoom; one following a walker inside the dead zone doesn't
        zoom = self._zoom(obs, pose, tt, vfov, any(self._moving[i] and not following[i] for i in (0, 1)))
        return Command(steps[0], steps[1], zoom, debug={
            "state": "moving" if any(self._moving) else "holding", "error": [round(e, 3) for e in error],
            "dead_zone": [round(d, 3) for d in dead], "velocity": [round(v, 3) for v in self._velocity],
            "target_speed": [round(v, 3) for v in target_speed], "limited": held})

    def _ramp(self, axis: int, wanted: float, accel: float, dt: float) -> float:
        """The speed after `dt` on the way to `wanted`: at most `accel` deg/s^2, and with Motion.jerk_time the
        acceleration itself builds up and eases off over that time (an S-curve). No time passed: no change."""
        v0 = self._velocity[axis]
        if self.motion.jerk_time <= 0:
            return max(v0 - accel * dt, min(v0 + accel * dt, wanted))
        jerk = accel / self.motion.jerk_time
        a0, dv = self._accel[axis], wanted - v0
        # brake early enough that the acceleration is back at 0 when the speed reaches the wish
        goal = math.copysign(min(accel, math.sqrt(2 * jerk * abs(dv))), dv) if dv else 0.0
        a1 = max(a0 - jerk * dt, min(a0 + jerk * dt, goal))
        v1 = v0 + a1 * dt
        if dt > 0 and (v1 - wanted) * (v0 - wanted) <= 0:  # reached (or passed) the wish
            v1, a1 = wanted, 0.0
        self._accel[axis] = a1
        return v1

    def _zoom(self, obs: Observation, pose: Pose, head_tilt: float, vfov: float, catching_up: bool = False) -> int:
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
        if catching_up or top <= 0:  # (0: no zooming, e.g. while on air)
            return 0
        if obs.cut_bottom and not obs.want_hfov and ratio > 1:
            # the picture's bottom edge cuts the person off: bigger than the box says, and zooming in only
            # cuts more off (it would never get there). Taken as reached.
            self._sizing = False
            return 0
        size = min(top, max(1, round(abs(math.log(ratio)) * 6)))
        zoom = size if ratio > 1 else -size
        limit = self.lens.zoom_max * 0.99 if self.max_zoom is None else min(self.max_zoom, self.lens.zoom_max * 0.99)
        if (zoom > 0 and pose.zoom >= limit) or (zoom < 0 and pose.zoom <= 1):
            self._sizing = False  # as far as the zoom goes: the one-time display size is done
            return 0
        if zoom > 0 and self.fix_tilt and abs(head_tilt - pose.tilt) > 0.35 * vfov:
            return 0  # (tilt fixed) zooming in would push the person out of the picture
        return zoom


def _hysteresis(table: list[float], last: int, step: int, speed: float, share: float) -> int:
    """Keep the last speed step until `speed` is past the middle between it and the neighbouring step by `share`
    of the gap (on a log scale), so the camera doesn't flap between two steps. Stops and reversals are never held."""
    if share <= 0 or step == 0 or last == 0 or (step > 0) != (last > 0) or step == last:
        return step
    i, speed = abs(last) - 1, abs(speed)
    if abs(step) > abs(last) and i + 1 < len(table):
        if speed < table[i] * (table[i + 1] / table[i]) ** (0.5 + share):
            return last
    elif abs(step) < abs(last) and 0 < i < len(table):
        if speed > table[i] / (table[i] / table[i - 1]) ** (0.5 + share):
            return last
    return step


FACE_MIN_CONF = 0.4  # face keypoints this certain count as seen


def head_point(box: list[float], keypoints: list[list[float]], min_conf: float = FACE_MIN_CONF) -> tuple[float, float]:
    """The point to frame: middle of the visible face keypoints, else near the top of the box."""
    face = [(x, y) for x, y, c in keypoints[:5] if c >= min_conf]  # nose, eyes, ears
    if face:
        return sum(p[0] for p in face) / len(face), sum(p[1] for p in face) / len(face)
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, y1 + 0.1 * (y2 - y1)


def cut_at_bottom(box: list[float], frame_height: float) -> bool:
    """Does the person's box reach the picture's bottom edge? Then the person goes on below it, and the box
    (only what is visible) is shorter than the person."""
    return box[3] >= frame_height * 0.99

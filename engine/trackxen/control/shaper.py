"""Smooth joystick moves: the middle layer between "drive at speed step N" and the camera.

A VISCA drive command runs at the asked speed at once, and neither the BirdDog P400 nor the PTZOptics G2
documents an acceleration or ramp setting. So a joystick press from rest to step 18 (57 deg/s on a P400) is a
whip, and letting go is a jolt. The shaper ramps the pan and tilt speeds (in deg/s, through the camera's speed
tables) to each new wish along an S-curve (smoothstep: the acceleration builds up and eases off), and sends the
speed steps on the way.

- A speed change takes at most `ramp` seconds (Pan/Tilt Smoothness: 0 at 1, i.e. sent as it is, as before; 0.5 s
  at 10), stopping included: letting go stops the camera within `ramp` (plus one 20 ms control tick).
- In picture terms a change is smaller in a wide shot: one of less than FULL_RAMP_SPEED of the picture per second
  takes a shorter time (proportionally), so slow moves in a wide shot don't lag, and starts in a tight shot are soft.
- Pan and tilt change together over the same time, so a diagonal move stays straight.
- A reversal goes through a stop (one control tick at step 0), not straight from one direction to the other.
- Zoom is passed as it is (it has no speed table here; the lens has its own ramp).
- reset() forgets the motion at once: safety stops (STOP ALL, the watchdog, tracking off) are never ramped.
"""

from __future__ import annotations

from trackxen.control.controller import SpeedTable

FULL_RAMP_SPEED = 0.5  # a speed change of this share of the picture per second (or more) takes the whole ramp


def smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


class ManualShaper:
    def __init__(self, speeds: SpeedTable):
        self.speeds = speeds
        self.goal = (0, 0)  # the wanted signed speed steps
        self._from = [0.0, 0.0]  # deg/s when the current change started
        self._to = [0.0, 0.0]
        self._start = 0.0
        self._length = 0.0  # s
        self._pass = True  # (ramp 0: the steps go out as they are)
        self._sent = (0, 0)

    @property
    def idle(self) -> bool:
        """At rest, and nothing under way."""
        return self.goal == (0, 0) and self._to == [0.0, 0.0] and (self._pass or self._length <= 0
                                                                     or self._from == [0.0, 0.0])

    def reset(self) -> None:
        """Stopped by something else (a safety stop): no motion, no change under way."""
        self.goal, self._from, self._to, self._length, self._pass = (0, 0), [0.0, 0.0], [0.0, 0.0], 0.0, True
        self._sent = (0, 0)

    def speed(self, now: float) -> list[float]:
        """The shaped speeds (deg/s) at `now`."""
        if self._length <= 0:
            return list(self._to)
        k = smoothstep((now - self._start) / self._length)
        return [a + (b - a) * k for a, b in zip(self._from, self._to)]

    def want(self, now: float, pan: int, tilt: int, ramp: float, fov: tuple[float, float] | None) -> None:
        """A new wish (signed speed steps). `ramp`: the longest a change may take (s); `fov`: the picture's
        horizontal and vertical field of view in degrees (None: not known, every change takes the whole ramp)."""
        if (pan, tilt) == self.goal and ramp > 0 and not self._pass:
            return
        current = self.speed(now)
        self.goal = (pan, tilt)
        self._to = [SpeedTable.speed_of(self.speeds.pan, pan), SpeedTable.speed_of(self.speeds.tilt, tilt)]
        self._pass = ramp <= 0
        if self._pass:
            self._from, self._length = list(self._to), 0.0
            return
        change = max(abs(b - a) / (f if fov else 1.0) for a, b, f in zip(current, self._to, fov or (1.0, 1.0)))
        share = 1.0 if fov is None else min(1.0, change / FULL_RAMP_SPEED)
        self._from, self._start, self._length = current, now, ramp * share
        if self._length <= 0:
            self._from = list(self._to)

    def steps(self, now: float) -> tuple[int, int]:
        """The speed steps to send at `now`."""
        if self._pass:
            self._sent = self.goal
            return self.goal
        if self._length > 0 and now - self._start >= self._length:  # there
            self._from, self._length = list(self._to), 0.0
        if self._length <= 0:
            out = self.goal
        else:
            pan, tilt = self.speed(now)
            out = SpeedTable.step_for(self.speeds.pan, pan), SpeedTable.step_for(self.speeds.tilt, tilt)
        out = tuple(0 if a * b < 0 else a for a, b in zip(out, self._sent))  # (reversing: a stop first)
        self._sent = out
        return out

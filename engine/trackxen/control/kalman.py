"""Constant-velocity Kalman filter for a target's direction in room angles (pan, tilt degrees).

Measurements carry the time the frame was captured, so the filter can predict where
the person will be when the next camera command actually takes effect.
"""

from __future__ import annotations

import numpy as np


class AngleKalman:
    def __init__(self, accel_noise: float = 20.0, measurement_noise: float = 0.25):
        self.accel_noise = accel_noise  # deg/s^2: how quickly a person can change speed
        self.measurement_noise = measurement_noise  # degrees
        self.x: np.ndarray | None = None  # pan, tilt, pan velocity, tilt velocity
        self.P = np.eye(4)
        self.t = 0.0

    @property
    def ready(self) -> bool:
        return self.x is not None

    def reset(self) -> None:
        self.x = None

    def _transition(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = self.accel_noise ** 2
        g = np.array([[dt ** 4 / 4, dt ** 3 / 2], [dt ** 3 / 2, dt ** 2]]) * q
        Q = np.zeros((4, 4))
        Q[np.ix_([0, 2], [0, 2])] = g
        Q[np.ix_([1, 3], [1, 3])] = g
        return F, Q

    def update(self, t: float, pan: float, tilt: float) -> None:
        z = np.array([pan, tilt])
        if self.x is None:
            self.x = np.array([pan, tilt, 0.0, 0.0])
            self.P = np.diag([1.0, 1.0, 25.0, 25.0])
            self.t = t
            return
        dt = t - self.t
        if dt > 0:
            F, Q = self._transition(dt)
            self.x = F @ self.x
            self.P = F @ self.P @ F.T + Q
            self.t = t
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        R = np.eye(2) * self.measurement_noise ** 2
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P

    def predict(self, t: float) -> tuple[float, float, float, float]:
        """Pan, tilt, pan speed, tilt speed at time t (state is not changed)."""
        assert self.x is not None
        dt = max(0.0, t - self.t)
        pan, tilt, vp, vt = self.x
        return float(pan + vp * dt), float(tilt + vt * dt), float(vp), float(vt)

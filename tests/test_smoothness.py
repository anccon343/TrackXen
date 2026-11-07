"""Smoothness regression: the repo's closed-loop simulation (tests/test_controller.simulate) with a noisy walker.

The full measurement (a fake P400/G2 with the real session, link and VISCA code, detector noise, head bob and sway)
is described in docs/development/progress.md; this is its fast, deterministic core: a person who keeps walking is followed in
one movement. Before 2026-11 the camera caught up, stopped while the person walked on, and started again once the
error had grown past the dead zone: 3-6 times in 6.5 s of walking here, at 20-70 % of the control ticks at step 0.
"""

import math
import random

import pytest
from test_controller import W, simulate

from trackxen.control.controller import Motion, SpeedTable
from trackxen.core.tracking_settings import smoothness_motion


def noisy_walker(speed: float, seed: int, start: float = 1.0, end: float = 9.0):
    """Walks right at `speed` deg/s from the framing point between `start` and `end` s, with detector noise and
    a vertical head bob (1.8 Hz)."""
    rng = random.Random(seed)

    def target(t: float) -> tuple[float, float]:
        x = min(max(t - start, 0.0), end - start) * speed
        bob = 0.08 * math.sin(2 * math.pi * 1.8 * t) if start < t < end else 0.0
        return x + rng.gauss(0, 0.05), -0.27 + bob + rng.gauss(0, 0.05)

    return target


@pytest.mark.parametrize("hfov", [8.0, 30.0, 50.0])
@pytest.mark.parametrize("speed", [1.5, 4.0])
def test_a_walking_person_is_followed_in_one_movement(hfov, speed):
    stopped, restarts = 0, 0
    for seed in (1, 2, 3):
        _, commands, _, _ = simulate(noisy_walker(speed, seed), seconds=11, start_zoom_hfov=hfov)
        first = next(c[0] for c in commands if c[1] != 0)  # (the person must first leave the dead zone)
        walk = [c for c in commands if first + 0.5 < c[0] < 8.9]
        restarts += sum(1 for a, b in zip(walk, walk[1:]) if a[1] == 0 and b[1] != 0)
        stopped += sum(1 for c in walk if c[1] == 0) / len(walk)
    assert restarts == 0
    assert stopped / 3 < 0.03


def stop_and_go(t: float) -> float:
    """Walks 2.5 s at 3 deg/s, stands 2 s, five times (from t = 1 s)."""
    x, t = -2.0, t - 1.0
    for _ in range(5):
        if t <= 0:
            return x
        if t < 2.5:
            return x + 3.0 * t
        x, t = x + 7.5, t - 2.5
        if t < 2.0:
            return x
        t -= 2.0
    return x


def noisy_stop_and_go(seed: int):
    rng = random.Random(seed)
    return lambda t: (stop_and_go(t) + rng.gauss(0, 0.04), -0.27 + rng.gauss(0, 0.04))


def commanded(commands, t0: float, t1: float) -> tuple[int, int, float]:
    """Step changes, jumps of more than one step, and the RMS acceleration (deg/s^2) of the commanded pan speed."""
    table = SpeedTable().pan
    sel = [c for c in commands if t0 < c[0] < t1]
    v = [SpeedTable.speed_of(table, c[1]) for c in sel]
    dv = [b - a for a, b in zip(v, v[1:])]
    jumps = sum(1 for a, b in zip(sel, sel[1:]) if abs(a[1] - b[1]) > 1)
    return sum(1 for d in dv if d), jumps, math.sqrt(sum((d / 0.02) ** 2 for d in dv) / len(dv))


@pytest.mark.parametrize("hfov, speed", [(8.0, 2.0), (30.0, 4.0)])
def test_a_higher_pan_tilt_smoothness_is_measurably_smoother(hfov, speed):
    """Lower levels mostly smooth the steady following (feed-forward low-pass, step hysteresis), higher ones mostly
    starts and stops (S-curve, less acceleration); the person stays well framed at the softest."""
    walk, stops = {}, {}
    for level in (1, 5, 10):
        motion = smoothness_motion(Motion(), level)
        w, g, worst = [0, 0, 0.0], [0, 0, 0.0], 0.0
        for seed in (1, 2, 3):
            path = noisy_walker(speed, seed)
            _, commands, _, _ = simulate(path, seconds=10, start_zoom_hfov=hfov, motion=motion)
            w = [a + b for a, b in zip(w, commanded(commands, 4.0, 9.0))]
            errors, commands, _, _ = simulate(noisy_stop_and_go(seed), seconds=23, start_zoom_hfov=hfov, motion=motion)
            g = [a + b for a, b in zip(g, commanded(commands, 2.0, 22.0))]
            worst = max(worst, max(abs(ex) for t, ex, _ in errors if t > 2.0) / W)
        walk[level], stops[level] = w, g
        assert worst < 0.2  # (the head 20 % of the picture's width from its place at most)
    assert walk[1][0] > walk[5][0] >= walk[10][0]  # step changes while walking
    assert stops[1][2] > stops[5][2] > stops[10][2]  # acceleration in stop-and-go
    assert stops[1][0] > stops[5][0] and stops[10][1] < stops[5][1]  # step changes; multi-step jumps (soft starts)

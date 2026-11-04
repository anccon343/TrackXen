"""Smoothness regression: the repo's closed-loop simulation (tests/test_controller.simulate) with a noisy walker.

The full measurement (a fake P400/G2 with the real session, link and VISCA code, detector noise, head bob and sway)
is described in docs/progress.md; this is its fast, deterministic core: a person who keeps walking is followed in
one movement. Before 2026-11 the camera caught up, stopped while the person walked on, and started again once the
error had grown past the dead zone: 3-6 times in 6.5 s of walking here, at 20-70 % of the control ticks at step 0.
"""

import math
import random

import pytest
from test_controller import simulate


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

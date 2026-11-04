"""The joystick's middle layer (control/shaper.py): speed changes ramped along an S-curve, bounded in time."""

import pytest

from trackxen.control.controller import SpeedTable
from trackxen.control.shaper import ManualShaper

WIDE, TELE = (60.0, 35.6), (3.2, 1.8)  # (horizontal, vertical) field of view, degrees


def run(shaper: ManualShaper, t0: float, seconds: float, dt: float = 0.02) -> list[tuple[float, int, int]]:
    out, t = [], t0
    while t <= t0 + seconds + 1e-9:
        out.append((round(t - t0, 3), *shaper.steps(t)))
        t += dt
    return out


def test_ramp_0_sends_the_steps_as_they_are():
    sh = ManualShaper(SpeedTable())
    sh.want(0.0, 18, -5, 0.0, WIDE)
    assert sh.steps(0.0) == (18, -5)
    sh.want(0.1, 0, 0, 0.0, WIDE)
    assert sh.steps(0.1) == (0, 0) and sh.idle


def test_a_start_and_a_stop_take_at_most_the_ramp_and_never_jump():
    sh = ManualShaper(SpeedTable())
    sh.want(0.0, 18, 0, 0.5, WIDE)  # 57 deg/s in a 60-degree picture: the whole ramp
    up = run(sh, 0.0, 0.6)
    pans = [p for _, p, _ in up]
    assert pans[0] == 0 and all(a <= b for a, b in zip(pans, pans[1:])) and len(set(pans)) > 10
    assert next(t for t, p, _ in up if p == 18) <= 0.5
    assert max(b - a for a, b in zip(pans, pans[1:])) <= 3  # (an S-curve: small steps at both ends)
    assert pans[1] - pans[0] <= 1 and pans[-1] == 18
    sh.want(1.0, 0, 0, 0.5, WIDE)  # let go
    down = run(sh, 1.0, 0.6)
    pans = [p for _, p, _ in down]
    assert all(a >= b >= 0 for a, b in zip(pans, pans[1:])) and len(set(pans)) > 10
    assert next(t for t, p, _ in down if p == 0) <= 0.5 and sh.idle


def test_a_small_change_in_a_wide_picture_is_quick_and_the_same_in_a_tight_one_soft():
    """Zoom-aware like tracking: what counts is the change in picture widths per second."""
    def time_to(fov, step):
        sh = ManualShaper(SpeedTable())
        sh.want(0.0, step, 0, 0.5, fov)
        return next(t for t, p, _ in run(sh, 0.0, 0.6) if p == step)

    assert time_to(WIDE, 5) <= 0.06 and time_to(TELE, 5) >= 0.3  # 2.9 deg/s: 5 % vs 90 % of the picture per second
    assert time_to(None, 5) >= 0.3  # (field of view not known: the whole ramp)


def test_a_reversal_goes_through_a_stop_and_a_diagonal_move_stays_straight():
    sh = ManualShaper(SpeedTable())
    sh.want(0.0, 10, 10, 0.5, WIDE)
    run(sh, 0.0, 0.6)
    sh.want(1.0, -10, 10, 0.5, WIDE)
    pans = [p for _, p, _ in run(sh, 1.0, 0.6)]
    assert 0 in pans and all(a >= b for a, b in zip(pans, pans[1:])) and pans[-1] == -10
    sh = ManualShaper(SpeedTable())
    sh.want(0.0, 12, 12, 0.5, WIDE)
    assert all(p == t for _, p, t in run(sh, 0.0, 0.6))  # (the same table: the same steps at every moment)


def test_reset_stops_at_once():
    sh = ManualShaper(SpeedTable())
    sh.want(0.0, 18, 6, 0.5, WIDE)
    run(sh, 0.0, 0.3)
    sh.reset()
    assert sh.idle and sh.steps(0.32) == (0, 0) and sh.speed(0.32) == [0.0, 0.0]
    sh.want(0.4, 18, 0, 0.5, WIDE)  # a new press starts from rest
    assert sh.steps(0.4) == (0, 0) and sh.speed(0.42)[0] == pytest.approx(57.0 * (3 * 0.04 ** 2 - 2 * 0.04 ** 3))

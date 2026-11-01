import bisect
from collections import deque

import pytest

from trackxen.control.camera import Pose
from trackxen.control.controller import Controller, Framing, Motion, Observation, SpeedTable, head_point
from trackxen.control.kalman import AngleKalman
from trackxen.control.lens import Lens

W, H = 1920, 1080


def test_lens_round_trips():
    lens = Lens()
    assert lens.hfov(0) == pytest.approx(60.0) and lens.hfov(lens.zoom_max) == pytest.approx(3.2)
    assert lens.zoom_for_hfov(lens.hfov(5000)) == pytest.approx(5000)
    assert lens.pixel_to_angles(W / 2, H / 2, W, H, 0) == (0.0, 0.0)
    dpan, dtilt = lens.pixel_to_angles(W, 0, W, H, 0)  # top-right corner
    assert dpan == pytest.approx(30.0) and dtilt > 0
    assert lens.angles_to_pixel(dpan, dtilt, W, H, 0) == pytest.approx((W, 0))


def test_speed_steps():
    table = SpeedTable().pan
    assert SpeedTable.step_for(table, 0.1) == 0
    assert SpeedTable.step_for(table, 2.0) == 4 and SpeedTable.step_for(table, -2.1) == -4
    assert SpeedTable.step_for(table, 500) == len(table)
    assert SpeedTable.speed_of(table, -4) == -2.0


def test_head_point():
    kp = [[0, 0, 0]] * 17
    assert head_point([100, 100, 200, 400], kp) == (150, 130)
    kp = [[150, 120, 0.9], [140, 115, 0.9], [160, 115, 0.9]] + [[0, 0, 0]] * 14
    assert head_point([100, 100, 200, 400], kp) == pytest.approx((150, 116.67), abs=0.01)


def test_kalman_estimates_speed():
    k = AngleKalman()
    for i in range(30):
        k.update(i / 30, 4.0 * i / 30, 1.0)
    pan, tilt, vp, vt = k.predict(1.0)
    assert vp == pytest.approx(4.0, abs=0.3) and pan == pytest.approx(4.0, abs=0.2) and abs(vt) < 0.2


def simulate(target, seconds=12.0, video_delay=0.08, command_delay=0.03, framing=None, motion=None,
             start_zoom_hfov=30.0, person_deg=7.0, setup=None):
    """Closed loop: simulated camera (speed table, delays) + controller following `target(t)`."""
    lens, speeds = Lens(), SpeedTable()
    ctrl = Controller(lens, speeds, framing or Framing(), motion or Motion())
    if setup:
        setup(ctrl)
    pan = tilt = 0.0
    zoom = lens.zoom_for_hfov(start_zoom_hfov)
    rate = [0.0, 0.0, 0.0]
    history: list[tuple[float, float, float, float]] = []
    pending: deque = deque()
    errors, commands, zooms = [], [], []
    dt, t = 0.005, 0.0
    next_frame = next_ctrl = 0.0

    def pose_at(when: float) -> Pose:
        i = bisect.bisect_left([h[0] for h in history], when)
        h = history[min(max(i, 0), len(history) - 1)]
        return Pose(*h)

    while t < seconds:
        while pending and pending[0][0] <= t:
            _, cmd = pending.popleft()
            rate = [SpeedTable.speed_of(speeds.pan, cmd.pan), SpeedTable.speed_of(speeds.tilt, cmd.tilt),
                    800.0 * cmd.zoom]
        pan += rate[0] * dt
        tilt += rate[1] * dt
        zoom = min(lens.zoom_max, max(0.0, zoom + rate[2] * dt))
        history.append((t, pan, tilt, zoom))
        if t >= next_frame and t > video_delay:
            next_frame += 1 / 30
            captured = t - video_delay
            pose = pose_at(captured)
            tp, tt = target(captured)
            x, y = lens.angles_to_pixel(tp - pose.pan, tt - pose.tilt, W, H, pose.zoom)
            height = person_deg / lens.vfov(pose.zoom) * H
            ctrl.observe(Observation(captured, x, y, height, W, H), pose)
            errors.append((captured, x - ctrl.framing.anchor_x * W, y - ctrl.framing.anchor_y * H))
        if t >= next_ctrl:
            next_ctrl += 1 / 50
            cmd = ctrl.command(t, Pose(t, pan, tilt, zoom))
            commands.append((t, cmd.pan, cmd.tilt, cmd.zoom))
            pending.append((t + command_delay, cmd))
        zooms.append(zoom)
        t += dt
    return errors, commands, zooms, (pan, tilt)


def test_centres_a_still_person():
    errors, _, _, (pan, tilt) = simulate(lambda t: (6.0, 2.0), seconds=6)
    _, ex, ey = errors[-1]
    framing = Framing()
    assert abs(ex) < framing.dead_zone_x * W and abs(ey) < framing.dead_zone_y * H
    assert pan > 3.0  # it really turned towards the person


def test_follows_a_walking_person():
    # Walks right at 4 deg/s (about 1 m/s at 15 m) for 8 s, then stands still
    def walker(t):
        return (min(t, 8.0) * 4.0 - 10.0, 0.0)

    errors, commands, _, (pan, _) = simulate(walker, seconds=12)
    settled = [abs(ex) for t, ex, _ in errors if 2.0 < t < 8.0]
    assert max(settled) < 0.15 * W, max(settled)  # never drifts far out of the framing point
    assert sum(settled) / len(settled) < 0.07 * W
    assert abs(errors[-1][1]) < Framing().dead_zone_x * W  # back on the mark after stopping
    assert pan == pytest.approx(22.0, abs=2.0)
    changes = sum(1 for a, b in zip(commands, commands[1:]) if a[1] != b[1])
    assert changes < 120  # smooth: no constant speed flipping


def test_does_not_move_for_small_sway():
    errors, commands, _, _ = simulate(lambda t: (0.3 * ((t * 2) % 2 - 1), -0.27), seconds=6)
    assert all(c[1] == 0 for c in commands[60:])  # sway stays inside the dead zone


def test_auto_zoom_brings_person_to_wanted_size():
    framing = Framing(display_size=True, auto_zoom=True, target_height=0.6)
    _, commands, zooms, _ = simulate(lambda t: (0.0, -0.27), seconds=10, framing=framing, start_zoom_hfov=50)
    assert any(c[3] > 0 for c in commands)  # zoomed in
    lens = Lens()
    size = 7.0 / lens.vfov(zooms[-1])
    assert size == pytest.approx(0.6, rel=0.2)


def test_display_size_without_auto_zoom_zooms_once():
    framing = Framing(display_size=True, auto_zoom=False, target_height=0.6)
    hold = {}
    _, commands, zooms, _ = simulate(lambda t: (0.0, -0.27), seconds=10, framing=framing, start_zoom_hfov=50,
                                     setup=lambda c: hold.setdefault("c", c))
    assert any(c[3] > 0 for c in commands) and not hold["c"]._sizing  # reached the size once
    hold["c"].framing.target_height = 0.3  # a new size is not chased without Auto Zoom
    assert hold["c"]._zoom(Observation(10.0, W / 2, H / 2, 0.6 * H, W, H), Pose(10.0, 0, 0, zooms[-1]), 0, 20) == 0


def test_fixed_tilt_never_tilts():
    _, commands, _, (_, tilt) = simulate(lambda t: (5.0, 6.0), seconds=5, setup=lambda c: setattr(c, "fix_tilt", True))
    assert all(c[2] == 0 for c in commands) and tilt == 0.0


def test_constrained_aim_stops_at_the_limit():
    def limit(pan, tilt, hfov, vfov):  # e.g. a shooting range edge: may not aim past 5 degrees
        return min(pan, 5.0), tilt

    errors, commands, _, (pan, _) = simulate(lambda t: (min(t, 6.0) * 4.0, 0.0), seconds=10,
                                              setup=lambda c: setattr(c, "constrain", limit))
    assert pan == pytest.approx(5.0, abs=1.0)
    assert all(c[1] == 0 for c in commands[-100:])  # parked at the limit, not pushing past it

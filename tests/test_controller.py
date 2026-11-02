import bisect
from collections import deque

import pytest

from trackxen.control.camera import Pose
from trackxen.control.controller import (Controller, Framing, Motion, Observation, SpeedTable, cut_at_bottom,
                                         head_point)
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
             start_zoom_hfov=30.0, person_deg=7.0, setup=None, lens=None, speeds=None):
    """Closed loop: simulated camera (speed table, delays) + controller following `target(t)`."""
    lens, speeds = lens or Lens(), speeds or SpeedTable()
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


# --- PTZOptics G2: speed step 1 is 1.7 deg/s, so slow wishes round to "no movement" --------------------

def ptz_lens_speeds() -> tuple[Lens, SpeedTable]:
    from trackxen.core.camera_models import MODELS

    m = MODELS["ptzoptics_g2"]
    return Lens(m["lens"]["hfov_wide"], m["lens"]["hfov_tele"], m["lens"]["zoom_max"]), SpeedTable(m["pan_speeds"],
                                                                                                    m["tilt_speeds"])


def ptz_controller(hfov: float, motion: Motion | None = None, framing: Framing | None = None):
    lens, speeds = ptz_lens_speeds()
    ctrl = Controller(lens, speeds, framing or Framing(), motion or Motion())
    zoom = lens.zoom_max if hfov <= lens.hfov_tele else lens.zoom_for_hfov(hfov)
    return ctrl, Pose(0.0, 0.0, 0.0, zoom)


def run_still(ctrl: Controller, pose: Pose, tilt_error: float, ticks: int = 100, height: float = 0.55 * H) -> list:
    """A still person whose head is `tilt_error` degrees above the framing point; the commands for it."""
    f = ctrl.framing
    off_pan, off_tilt = ctrl.lens.pixel_to_angles(f.anchor_x * W, f.anchor_y * H, W, H, pose.zoom)
    x, y = ctrl.lens.angles_to_pixel(off_pan, off_tilt + tilt_error, W, H, pose.zoom)
    cmds = []
    for i in range(ticks):
        t = i * 0.02
        ctrl.observe(Observation(t, x, y, height, W, H), Pose(t, pose.pan, pose.tilt, pose.zoom))
        cmds.append(ctrl.command(t, Pose(t, pose.pan, pose.tilt, pose.zoom)))
    return cmds


def test_a_correction_slower_than_step_1_still_moves_the_camera():
    """N14: tilt error outside the dead zone (0.36 deg at 8 deg wide) but so small that gain x error is below
    half of step 1: the camera must still be moved (slowest step), not left "moving" at step 0 forever."""
    ctrl, pose = ptz_controller(8.0)
    cmds = run_still(ctrl, pose, 0.45)
    assert cmds[-1].tilt == 1 and all(c.pan == 0 for c in cmds)
    ctrl, pose = ptz_controller(8.0)
    assert run_still(ctrl, pose, -0.45)[-1].tilt == -1


def test_a_small_error_inside_the_dead_zone_ends_the_movement_and_lets_it_zoom():
    """N14: moving, the error is inside the dead zone but above the settle threshold, and the speed rounds to
    step 0: the axis settles (it would never get closer), so Display Size / Auto Zoom can zoom."""
    ctrl, pose = ptz_controller(20.0, framing=Framing(display_size=True, auto_zoom=True, target_height=0.55))
    ctrl._moving = [False, True]  # (after a correction)
    cmds = run_still(ctrl, pose, 0.3, ticks=20, height=0.25 * H)  # dead zone 0.9 deg, settled below 0.27
    assert not ctrl._moving[1] and all(c.tilt == 0 for c in cmds) and cmds[-1].zoom > 0


def test_on_air_speed_limit_below_step_1_still_corrects_tilt():
    """K53: at full tele with the on-air speed (0.5), the tilt limit is below half of step 1 (0.85 deg/s)."""
    ctrl, pose = ptz_controller(3.36, motion=Motion(max_pan=15.0, max_tilt=7.5))
    cmds = run_still(ctrl, pose, -0.756)  # the head at 0.7 of the picture height
    assert cmds[-1].tilt == -1  # down, at the slowest step


def test_closed_loop_ptzoptics_tight_shot_settles_and_zooms():
    """N14 in a closed loop with camera latency: the tilt settles inside the dead zone and auto zoom works."""
    lens, speeds = ptz_lens_speeds()
    framing = Framing(display_size=True, auto_zoom=True, target_height=0.6)
    errors, commands, _, _ = simulate(lambda t: (0.5, 0.3), seconds=10, framing=framing, start_zoom_hfov=8.0,
                                      person_deg=1.5, command_delay=0.1, video_delay=0.1, lens=lens, speeds=speeds)
    assert any(c[3] > 0 for c in commands)  # zoomed in towards the display size
    _, ex, ey = errors[-1]
    assert abs(ex) < framing.dead_zone_x * W and abs(ey) < framing.dead_zone_y * H


def test_two_commands_at_the_same_moment_keep_the_acceleration_limit():
    """K56: dt == 0 means no time passed, so the speed may not change (not: any change allowed)."""
    ctrl = Controller()
    pose = Pose(0.0, 0.0, 0.0, ctrl.lens.zoom_for_hfov(40.0))
    x, y = ctrl.lens.angles_to_pixel(10.0, 0.0, W, H, pose.zoom)
    ctrl.observe(Observation(0.0, x, y, 400, W, H), pose)
    ctrl.command(0.0, pose)
    first = ctrl._velocity[0]
    assert 0 < first <= Motion().max_accel * 0.02 + 1e-9
    ctrl.command(0.0, pose)
    assert ctrl._velocity[0] == pytest.approx(first)


def test_display_size_at_the_zoom_limit_counts_as_done():
    """N12: the one-time Display Size zoom stopped by the zoom limit is over; it does not keep adjusting
    later like Auto Zoom."""
    ctrl = Controller(framing=Framing(display_size=True, auto_zoom=False, target_height=0.6))
    tele, mid, wide = (Pose(0.0, 0.0, 0.0, z) for z in (ctrl.lens.zoom_max, ctrl.lens.zoom_max / 2, 0.0))
    assert ctrl._zoom(Observation(0.0, W / 2, H / 2, 0.2 * H, W, H), tele, 0.0, 2.0) == 0  # too small, can't zoom in
    assert not ctrl._sizing
    assert ctrl._zoom(Observation(1.0, W / 2, H / 2, 0.95 * H, W, H), mid, 0.0, 10.0) == 0  # (walked up close)
    ctrl.reset()
    assert ctrl._zoom(Observation(0.0, W / 2, H / 2, 0.95 * H, W, H), wide, 0.0, 30.0) == 0 and not ctrl._sizing
    ctrl.framing.auto_zoom = True  # Auto Zoom keeps adjusting
    assert ctrl._zoom(Observation(1.0, W / 2, H / 2, 0.95 * H, W, H), mid, 0.0, 10.0) < 0


def test_a_person_cut_off_by_the_bottom_of_the_picture_is_not_zoomed_into():
    """N15: the box ends at the picture's bottom edge, so the person is bigger than it shows; zooming in would
    only cut more off (and never reach the size). Zooming out still works."""
    ctrl = Controller(framing=Framing(display_size=True, auto_zoom=True, target_height=0.9))
    pose = Pose(0.0, 0.0, 0.0, ctrl.lens.zoom_max / 2)
    cut = Observation(0.0, W / 2, 0.3 * H, 0.72 * H, W, H, cut_bottom=True)
    assert ctrl._zoom(cut, pose, 0.0, 10.0) == 0
    assert ctrl._zoom(Observation(0.0, W / 2, 0.3 * H, 0.72 * H, W, H), pose, 0.0, 10.0) > 0  # whole person: zoom in
    sit = Observation(0.0, W / 2, 0.3 * H, 0.72 * H, W, H, target_height=0.95, cut_bottom=True)
    assert ctrl._zoom(sit, pose, 0.0, 10.0) == 0
    small = Controller(framing=Framing(display_size=True, auto_zoom=True, target_height=0.3))
    assert small._zoom(cut, pose, 0.0, 10.0) < 0  # too big: zooms out


def test_cut_at_bottom():
    assert cut_at_bottom([100, 300, 200, 1079], 1080) and cut_at_bottom([100, 300, 200, 1085], 1080)
    assert not cut_at_bottom([100, 300, 200, 1000], 1080)


def test_session_tells_the_controller_when_the_box_is_cut_at_the_bottom():
    """N15: the session passes it on with each sighting."""
    import time

    from trackxen.core.config import CameraConfig
    from trackxen.core.session import CameraSession

    class Link:
        visca_ok, freed_age, commanding, last_motion = True, 0.01, False, 0.0

        def pose_at(self, t):
            return Pose(t, 0.0, 0.0, 2266.0)

        def pose_now(self):
            return Pose(time.monotonic(), 0.0, 0.0, 2266.0)

        def request(self, pan, tilt, zoom=0):
            pass

    s = CameraSession(CameraConfig(id="cam1", video_delay=0.0), Link())
    s.set_tracking(True)
    s.select_id(1)
    for bottom, cut in ((900, False), (1080, True)):
        now = time.monotonic()
        kp = [[960, 330, 0.9]] + [[0, 0, 0]] * 16
        s.on_tracks({"t_frame": now, "t_done": now, "width": W, "height": H, "timing": {}, "tracks": [
            {"id": 1, "box": [900, 300, 1020, bottom], "score": 0.9, "keypoints": kp, "velocity": [0, 0],
             "missed": 0, "age": 5.0}]})
        assert s.controller.last_obs.cut_bottom is cut

import math

import pytest

from trackxen.control.camera import Pose
from trackxen.control.lens import Lens
from trackxen.core.tracking_settings import AppSettings, merge
from trackxen.rules.areas import Placed
from trackxen.rules.framing import Framer, facing_yaw, posture_ratio

LENS = Lens()
POSE = Pose(0.0, 0.0, 0.0, LENS.zoom_for_hfov(40.0))
W, H = 1920, 1080
MSG = {"width": W, "height": H}


def kp(**points) -> list[list[float]]:
    """17 keypoints, all unseen except the named ones: name=(x, y)."""
    names = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
             "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]
    out = [[0.0, 0.0, 0.0] for _ in names]
    for name, (x, y) in points.items():
        out[names.index(name)] = [x, y, 0.9]
    return out


def person(pid: int, pan: float, height: float = 7.0, width: float = 1.6, keypoints=None) -> Placed:
    """A person whose head is at `pan` degrees, as place() would report them."""
    x = LENS.angles_to_pixel(pan, 0, W, H, POSE.zoom)[0]
    track = {"id": pid, "box": [x - 40, 300, x + 40, 900], "keypoints": keypoints or kp(nose=(x, 330)),
             "missed": 0, "age": 5.0}
    return Placed(pid, pan, 0.0, track, pan - width / 2, pan + width / 2, height)


def slot(**values) -> AppSettings:
    data = AppSettings().model_dump()
    merge(data, values)
    return AppSettings.model_validate(data)


def run(framer, s, main, people, seconds, t0=0.0, zones=None, pose=POSE):
    shot, t = None, t0
    while t < t0 + seconds:
        shot = framer.shot(s, main, people, zones or {}, MSG, pose, LENS, t)
        t += 1 / 30
    return shot, t


def test_face_direction_from_keypoints():
    assert facing_yaw(kp(nose=(500, 300), l_ear=(470, 300), r_ear=(530, 300))) == pytest.approx(0.0)
    assert facing_yaw(kp(nose=(480, 300), l_ear=(470, 300), r_ear=(530, 300))) < -0.5  # turned to picture-left
    assert facing_yaw(kp(nose=(560, 300), l_ear=(530, 300))) == 1.0  # profile, looking right
    assert facing_yaw(kp(l_ear=(530, 300))) is None  # no nose: unknown


def test_posture_from_keypoints():
    standing = kp(l_shoulder=(500, 400), l_hip=(500, 600), l_knee=(500, 790))
    sitting = kp(l_shoulder=(500, 400), l_hip=(500, 600), l_knee=(600, 630))
    assert posture_ratio(standing) == pytest.approx(0.95)
    assert posture_ratio(sitting) < 0.2
    assert posture_ratio(kp(l_shoulder=(500, 400))) is None  # hips/knees hidden (behind a desk)


def test_nearby_person_joins_the_group_and_the_view_widens():
    s = slot(composition={"display_size": True, "target_height": 0.55},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main, other = Framer(), person(1, 0.0), person(2, 20.0)
    shot, t = run(framer, s, main, [main, other], 0.3)
    assert shot.group == [] and shot.want_hfov is None  # not long enough yet: single composition
    shot, t = run(framer, s, main, [main, other], 1.5, t)
    assert shot.group == [2] and framer.blend == 1.0
    assert shot.want_hfov == pytest.approx((20.8 - (-0.8)) * 1.3)  # both people plus the margin
    assert shot.anchor[0] == 0.5  # the group is centred
    head = LENS.pixel_to_angles(shot.x, shot.y, W, H, POSE.zoom)[0]
    assert head == pytest.approx(0.0, abs=0.05)  # the motion filter keeps following the target...
    assert head + shot.offset[0] == pytest.approx(10.0, abs=0.05)  # ...and the aim is between the two


def test_group_member_keeps_its_place_when_the_tracker_renumbers_them():
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True}})
    framer, main = Framer(), person(1, 0.0)
    shot, t = run(framer, s, main, [main, person(2, 4.0)], 2.0)
    assert shot.group == [2]
    shot, t = run(framer, s, main, [main, person(7, 4.5)], 0.1, t)  # same person, new ID
    assert shot.group == [7] and framer.blend == 1.0


def test_a_small_group_never_zooms_in_past_the_single_composition():
    s = slot(composition={"display_size": True, "target_height": 0.55},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0)
    shot, _ = run(framer, s, main, [main, person(2, 3.0)], 2.0)
    assert shot.group == [2] and shot.want_hfov == pytest.approx(LENS.hfov_for_vfov(7.0 / 0.55))


def test_group_dissolves_when_the_other_walks_away_and_zoom_returns():
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0)
    shot, t = run(framer, s, main, [main, person(2, 4.0)], 2.0)
    assert shot.group == [2]
    far = person(2, 40.0)  # too far to keep the main person big enough
    wide = Pose(0.0, 0.0, 0.0, LENS.zoom_for_hfov(55.0))  # the camera zoomed out for the group
    shot, t = run(framer, s, main, [main, far], 0.5, t, pose=wide)
    assert shot.group == [2]  # patience: not dropped at once
    shot, t = run(framer, s, main, [main, far], 2.0, t, pose=wide)
    assert shot.group == [] and framer.blend == 0.0
    assert shot.want_hfov == pytest.approx(40.0, rel=0.01)  # back to the view from before the group
    shot, t = run(framer, s, main, [main, far], 0.1, t)  # once there, the normal composition again
    assert shot.want_hfov is None and framer.home_hfov is None


def test_people_in_halting_or_exclusion_areas_are_never_framed():
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True}})
    framer, main, other = Framer(), person(1, 0.0), person(2, 3.0)
    shot, _ = run(framer, s, main, [main, other], 3.0, zones={2: "halting"})
    assert shot.group == []


def test_minimum_size_limits_how_far_the_group_reaches():
    near_only = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "min_size": 3}})
    shot, _ = run(Framer(), near_only, person(1, 0.0), [person(1, 0.0), person(2, 20.0)], 2.0)
    assert shot.group == []  # 20 degrees away would make the main person smaller than 45 % of the picture
    wide = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "min_size": 0}})
    shot, _ = run(Framer(), wide, person(1, 0.0), [person(1, 0.0), person(2, 20.0)], 2.0)
    assert shot.group == [2]


def test_face_direction_leaves_space_in_front():
    s = slot(auto_framing={"enabled": True, "face_direction": {"enabled": True, "sensitivity": 3, "space": 2}})
    x = LENS.angles_to_pixel(0, 0, W, H, POSE.zoom)[0]
    looking_left = person(1, 0.0, keypoints=kp(nose=(x - 30, 330), l_ear=(x - 5, 330)))
    shot, t = run(Framer(), s, looking_left, [looking_left], 3.0)
    assert shot.facing == "left"
    assert shot.anchor[0] == pytest.approx(0.5 + 0.10)  # placed right of centre: room on the left


def test_sitting_down_zooms_in_and_standing_up_out():
    s = slot(auto_framing={"enabled": True, "sit_stand": {"enabled": True, "sit_size": 0.8, "stand_size": 0.5}})
    assert s.composition.display_size and s.composition.auto_zoom and s.composition.target_height == 0.5
    standing = kp(nose=(960, 330), l_shoulder=(960, 400), l_hip=(960, 600), l_knee=(960, 790))
    sitting = kp(nose=(960, 500), l_shoulder=(960, 560), l_hip=(960, 760), l_knee=(1060, 790))
    framer = Framer()
    shot, t = run(framer, s, person(1, 0.0, keypoints=standing), [], 0.2)
    assert shot.posture == "standing" and shot.target_height == 0.5
    shot, t = run(framer, s, person(1, 0.0, keypoints=sitting), [], 0.3, t)
    assert shot.posture == "standing"  # not after a moment's bend
    shot, t = run(framer, s, person(1, 0.0, keypoints=sitting), [], 1.0, t)
    assert shot.posture == "sitting" and shot.target_height == 0.8


def test_group_never_needs_more_than_the_widest_view():
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "min_size": 0}})
    shot, _ = run(Framer(), s, person(1, 0.0), [person(1, 0.0), person(2, 45.0)], 2.0)
    assert shot.group == []  # 46 degrees x margin is wider than the 60-degree lens can show


def test_nobody_joins_while_the_target_is_at_the_edge_of_the_picture():
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True}})
    main = person(1, 18.5)  # 40-degree view centred at 0: the target is near the right edge
    shot, _ = run(Framer(), s, main, [main, person(2, 15.0)], 2.0)
    assert shot.group == []


def at_hfov(hfov: float) -> Pose:
    return Pose(0.0, 0.0, 0.0, LENS.zoom_for_hfov(hfov))


def test_the_return_after_a_group_ends_where_the_controller_stops_zooming():
    """N16: the controller stops zooming within its zoom dead band (15 %); the way back to the view from
    before the group must count as done there, or it keeps pulling the zoom (and undoes a hand zoom)."""
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0)
    shot, t = run(framer, s, main, [main, person(2, 4.0)], 2.0)
    assert shot.group == [2] and framer.home_hfov == pytest.approx(40.0)
    shot, t = run(framer, s, main, [main], 2.0, t, pose=at_hfov(45.0))  # the controller stopped 12 % short
    assert framer.blend == 0.0 and shot.want_hfov is None and framer.home_hfov is None
    shot, t = run(framer, s, main, [main], 0.5, t, pose=at_hfov(20.0))  # the operator zooms in by hand
    assert shot.want_hfov is None  # left alone


def test_display_size_without_auto_zoom_returns_to_the_display_size_after_a_group():
    """N17: with Display Size (one-time, no Auto Zoom) the single composition after a group is the display
    size again; nothing else would zoom back from the group's width."""
    s = slot(composition={"display_size": True, "auto_zoom": False, "target_height": 0.55},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0)
    shot, t = run(framer, s, main, [main, person(2, 20.0)], 2.0)
    assert shot.group == [2]
    sized = LENS.hfov_for_vfov(7.0 / 0.55)
    shot, t = run(framer, s, main, [main], 2.0, t, pose=at_hfov(26.0))  # the camera is at the group's width
    assert framer.blend == 0.0 and shot.want_hfov == pytest.approx(sized)
    shot, t = run(framer, s, main, [main], 0.1, t, pose=at_hfov(sized * 1.05))  # there (within the dead band)
    assert shot.want_hfov is None and framer.home_hfov is None
    shot, t = run(framer, s, main, [main], 0.5, t, pose=at_hfov(26.0))  # later zoomed by hand: left alone
    assert shot.want_hfov is None


def test_the_return_after_a_group_ends_at_the_wide_end_when_the_size_needs_more():
    s = slot(composition={"display_size": True, "auto_zoom": False, "target_height": 0.2},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0, height=20.0)  # the display size would need a 70-degree view
    shot, t = run(framer, s, main, [main, person(2, 4.0)], 2.0)
    assert shot.group == [2]
    shot, t = run(framer, s, main, [main], 2.0, t, pose=Pose(0.0, 0.0, 0.0, 0.0))  # zoomed all the way out
    assert shot.want_hfov is None and framer.home_hfov is None  # as far as it goes: done


def test_auto_zoom_handles_the_return_itself():
    s = slot(composition={"display_size": True, "auto_zoom": True, "target_height": 0.55},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0)
    shot, t = run(framer, s, main, [main, person(2, 20.0)], 2.0)
    shot, t = run(framer, s, main, [main], 2.0, t, pose=at_hfov(26.0))
    assert framer.blend == 0.0 and shot.want_hfov is None and framer.home_hfov is None


def test_hfov_for_a_view_taller_than_180_degrees_stays_positive():
    """K58: tan() wraps above 180 degrees; the widest possible view is the answer instead."""
    for vfov in (179.0, 182.0, 200.0, 400.0):
        assert 170.0 < LENS.hfov_for_vfov(vfov) < 180.0
    assert LENS.hfov_for_vfov(36.0) == pytest.approx(math.degrees(2 * math.atan(math.tan(math.radians(18)) * 16 / 9)))


def test_a_very_big_main_person_still_forms_a_group_and_never_crashes():
    """K58: a main person taller than the minimum share of 180 degrees (an extreme close-up at the wide
    end) must not make the group's widest view negative, nor the single view's log() fail."""
    wide = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "min_size": 0}})
    main = person(1, 0.0, height=30.0)
    shot, _ = run(Framer(), wide, main, [main, person(2, 3.0)], 2.0)
    assert shot.group == [2]
    sitting = kp(nose=(960, 500), l_shoulder=(960, 560), l_hip=(960, 760), l_knee=(1060, 790))
    s = slot(auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3},
                           "sit_stand": {"enabled": True, "sit_size": 0.2}})
    main = person(1, 0.0, height=37.0, keypoints=sitting)
    shot, _ = run(Framer(), s, main, [main, person(2, 3.0)], 2.0)
    assert shot.group == [2] and shot.want_hfov > 0


def test_a_main_person_cut_off_at_the_bottom_does_not_narrow_the_single_view():
    """N15: the box ends at the picture's bottom edge (the person goes on below it): sizing from it would
    zoom in far past the display size, so the single view is never narrower than the view now."""
    s = slot(composition={"display_size": True, "auto_zoom": False, "target_height": 0.9},
             auto_framing={"enabled": True, "multi_person": {"enabled": True, "transition_speed": 3}})
    framer, main = Framer(), person(1, 0.0, height=10.0)
    sized = LENS.hfov_for_vfov(10.0 / 0.9)
    assert framer._single_hfov(s.composition, None, main, POSE, LENS) == pytest.approx(sized)
    assert framer._single_hfov(s.composition, None, main, POSE, LENS, cut=True) == pytest.approx(40.0)
    main.track["box"][3] = H  # cut off by the bottom of the picture
    shot, t = run(framer, s, main, [main, person(2, 20.0)], 2.0)
    assert shot.group == [2]
    shot, t = run(framer, s, main, [main], 2.0, t)
    assert framer.blend == 0.0 and shot.want_hfov is None  # stays at the view it has (40), no zooming in


def test_the_framed_point_does_not_jump_when_the_face_is_lost():
    """C6: looking down at notes or turning away, the face keypoints drop out and the point falls back to near the
    top of the box. That fallback is put at the height the face has had in the box (here 0.25 of it), not at a fixed
    0.1: a jump of 0.15 x 600 px, a step of the tilt the camera would follow."""
    framer, s = Framer(), slot()
    x = LENS.angles_to_pixel(0.0, 0, W, H, POSE.zoom)[0]
    face = person(1, 0.0, keypoints=kp(nose=(x, 450), l_eye=(x - 10, 445), r_eye=(x + 10, 445)))
    shot, t = run(framer, s, face, [face], 2.0)
    assert shot.y == pytest.approx(446.67, abs=0.01)
    lost = person(1, 0.0, keypoints=kp(l_shoulder=(x - 60, 520)))  # no face keypoints
    assert framer.shot(s, lost, [lost], {}, MSG, POSE, LENS, t).y == pytest.approx(446.67, abs=0.5)
    assert Framer().shot(s, lost, [lost], {}, MSG, POSE, LENS, t).y == pytest.approx(360.0)  # never seen: 0.1

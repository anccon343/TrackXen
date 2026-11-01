import pytest

from trackxen.control.camera import Pose
from trackxen.control.lens import Lens
from trackxen.core.tracking_settings import AppSettings, Area, merge
from trackxen.rules import areas as rules
from trackxen.rules.geometry import area_from_view, area_to_view, room_to_view, view_edges, view_to_room

LENS = Lens()
POSE = Pose(0.0, 12.0, -4.0, 3000.0)


def slot(**values) -> AppSettings:
    s = AppSettings()
    data = s.model_dump()
    merge(data, values)
    return AppSettings.model_validate(data)


def test_picture_and_room_angles_round_trip():
    for x, y in [(0.5, 0.5), (0.1, 0.2), (0.9, 0.85)]:
        pan, tilt = view_to_room(x, y, POSE, LENS)
        bx, by = room_to_view(pan, tilt, POSE, LENS)
        assert abs(bx - x) < 1e-9 and abs(by - y) < 1e-9
    assert view_to_room(0.5, 0.5, POSE, LENS) == (12.0, -4.0)


def test_area_drawn_on_the_picture_is_shown_in_the_same_place():
    area = area_from_view(0.2, 0.3, 0.6, 0.8, POSE, LENS)
    assert area.left < 12 < area.right and area.bottom < area.top
    rect = area_to_view(area, POSE, LENS)
    assert all(abs(a - b) < 0.02 for a, b in zip(rect, (0.2, 0.3, 0.6, 0.8)))


def test_area_behind_the_camera_is_not_drawn():
    behind = Area(enabled=True, left=150, right=170, top=10, bottom=-10)
    assert area_to_view(behind, POSE, LENS) is None
    huge = Area(enabled=True, left=-170, right=170, top=90, bottom=-90)  # a whole-room shooting range
    x1, y1, x2, y2 = area_to_view(huge, POSE, LENS)
    assert x1 < 0 and x2 > 1 and y1 < 0 and y2 > 1


def test_view_edges():
    edges = view_edges(POSE, LENS)
    hfov = LENS.hfov(POSE.zoom)
    assert abs(edges["right"] - edges["left"] - hfov) < 1e-9 and edges["top"] > edges["bottom"]


def test_shooting_range_keeps_the_picture_inside():
    s = slot(shooting_range={"enabled": True, "left": -30, "right": 30, "top": 20, "bottom": -20})
    assert rules.clamp_to_range(s, 25, 0, 40, 22.5) == (10, 0)  # right edge would be at 45
    assert rules.clamp_to_range(s, -25, 15, 40, 22.5) == (-10, 8.75)
    assert rules.clamp_to_range(s, 0, 0, 80, 22.5)[0] == 0  # view wider than the range: centred
    assert rules.clamp_to_range(slot(), 100, 50, 40, 22.5) == (100, 50)  # range off


def test_priority_display_area_stays_in_the_picture():
    s = slot(priority_area={"enabled": True, "left": 10, "right": 20, "top": 5, "bottom": -5})
    pan, tilt = rules.keep_priority_in_view(s, 0, 0, 40, 22.5)
    assert pan == pytest.approx(2.0)  # right edge 20 + 5 % margin = 22 must fit: pan >= 2
    assert tilt == 0
    assert rules.keep_priority_in_view(s, 15, 0, 40, 22.5) == (15, 0)  # already inside: untouched
    zoom = rules.max_zoom_for_priority(s, LENS)
    assert LENS.hfov(zoom) == pytest.approx(LENS.hfov_for_vfov(11.0))  # 10 degrees tall + margins sets it


def test_view_for_area_prefers_the_angle_it_was_drawn_at():
    area = Area(enabled=True, left=-10, right=10, top=5, bottom=-5)
    view = rules.view_for_area(area, LENS)
    assert view.pan == 0 and view.tilt == 0 and LENS.hfov(view.zoom) == pytest.approx(23.0)
    area.view.set, area.view.pan = True, 3.0
    assert rules.view_for_area(area, LENS).pan == 3.0


def test_auto_selector_waits_and_prefers_whoever_was_there_first():
    s = slot(auto_select={"enabled": True, "start_time": 2.0})
    a, b = rules.Placed(1, 0, 0, {}), rules.Placed(2, 5, 0, {})
    sel = rules.AutoSelector()
    assert sel.update(s, [a], 0.0) is None
    assert sel.update(s, [a, b], 1.0) is None
    assert sel.update(s, [a, b], 2.0) == 1
    assert sel.progress(s, 2.0) == {1: 0.0, 2: 1.0}
    assert sel.update(s, [b], 2.5) is None  # 1 left; 2 still has to wait
    assert sel.update(s, [b], 3.0) == 2


def test_halting_beats_exclusion_beats_auto_select():
    s = slot(halting_areas={"0": {"enabled": True, "left": -1, "right": 1, "top": 1, "bottom": -1}},
             exclusion_areas={"0": {"enabled": True, "left": -5, "right": 5, "top": 5, "bottom": -5}},
             auto_select={"enabled": True, "area": {"enabled": True, "left": -10, "right": 10, "top": 10, "bottom": -10}})
    assert rules.zone_of(s, rules.Placed(1, 0, 0, {})) == "halting"
    assert rules.zone_of(s, rules.Placed(1, 3, 0, {})) == "excluded"
    assert rules.selectable(s, rules.Placed(1, 8, 0, {}))
    assert not rules.selectable(s, rules.Placed(1, 12, 0, {}))  # outside the auto select area


def test_area_rules_are_reported():
    s = slot(priority_area={"enabled": True, "left": 40, "right": 50, "top": 5, "bottom": -5},
             fixed_areas={"2": {"enabled": True, "left": 100, "right": 120, "top": 5, "bottom": -5}},
             shooting_range={"enabled": True, "left": -60, "right": 60},
             initial={"set": True, "pan": 0, "tilt": 0, "zoom": 0})
    problems = s.problems(LENS)
    assert any("can't be used together" in p for p in problems)
    assert any("Fixed viewing angle area 3 is outside the shooting range" in p for p in problems)
    assert any("priority display area is outside the initial position" in p for p in problems)
    assert slot().problems(LENS) == []


def test_settings_patch():
    data = AppSettings().model_dump()
    merge(data, {"sensitivity": 3, "auto_select": {"start_time": 1.5}, "halting_areas": {"3": {"enabled": True}}})
    s = AppSettings.model_validate(data)
    assert s.sensitivity == 3 and s.auto_select.start_time == 1.5 and s.halting_areas[3].enabled
    with pytest.raises(ValueError):
        merge(AppSettings().model_dump(), {"sensitivty": 3})
    with pytest.raises(ValueError):  # out of Canon's range
        AppSettings.model_validate({**AppSettings().model_dump(), "sensitivity": 11})

import json
import math

import pytest
from pydantic import ValidationError

from trackxen.core.tracking_settings import AppSettings, View, merge


def test_composition_values_the_pages_use_are_accepted():
    s = AppSettings.model_validate({"composition": {"anchor_x": 0.15, "anchor_y": 0.7, "dead_zone_x": 0.01,
                                                    "dead_zone_y": 0.3, "target_height": 0.95}})
    assert s.composition.anchor_x == 0.15 and s.composition.target_height == 0.95
    assert AppSettings.model_validate({"composition": {"anchor_x": 0.85, "anchor_y": 0.1, "target_height": 0.2}})


@pytest.mark.parametrize("values", [{"target_height": 0}, {"zoom_dead_band": -1}, {"anchor_x": 3.0},
                                    {"anchor_y": -0.5}, {"dead_zone_x": -0.5}, {"dead_zone_y": 0.9},
                                    {"anchor_x": float("nan")}, {"anchor_x": "nan"}, {"target_height": float("inf")}])
def test_composition_values_out_of_range_are_refused(values):
    with pytest.raises(ValidationError) as e:
        AppSettings.model_validate({"composition": values})
    json.dumps(e.value.errors(include_url=False))  # the settings pages get a 422, not a 500


def test_settings_numbers_must_be_finite():
    for bad in (float("nan"), float("inf"), "-inf"):
        with pytest.raises(ValidationError):
            View.model_validate({"set": True, "pan": bad})
        with pytest.raises(ValidationError):
            AppSettings.model_validate({"shooting_range": {"left": bad}})
        with pytest.raises(ValidationError):
            AppSettings.model_validate({"priority_area": {"top": bad}})
    assert math.isfinite(View(pan=12.5).pan)


def test_a_malformed_settings_change_is_a_bad_value_not_a_crash():
    for patch in ({"exclusion_areas": {"1": True}}, {"composition": {"anchor_x": 0.5}, "fixed_areas": {"0": "abc"}}):
        data = AppSettings().model_dump()
        with pytest.raises(ValueError):
            merge(data, patch)
    for patch in ("abc", None, [1, 2]):
        with pytest.raises(ValueError):
            merge(AppSettings().model_dump(), patch)


def test_pan_tilt_smoothness_is_1_to_10_and_defaults_to_5():
    assert AppSettings().smoothness == 5
    assert AppSettings.model_validate({"smoothness": 1}).smoothness == 1
    assert AppSettings.model_validate({"smoothness": 10}).smoothness == 10
    for bad in (0, 11, 2.5, "soft", float("nan"), None):
        with pytest.raises(ValidationError) as e:
            AppSettings.model_validate({"smoothness": bad})
        json.dumps(e.value.errors(include_url=False))  # a 422 for the settings pages
    old = AppSettings().model_dump()
    del old["smoothness"]  # settings saved before it existed
    assert AppSettings.model_validate(old).smoothness == 5
    data = AppSettings().model_dump()
    merge(data, {"smoothness": 8})  # a live change from the page
    assert AppSettings.model_validate(data).smoothness == 8


def test_smoothness_scales_the_camera_motion_profile():
    """1 is the liveliest (no feed-forward low-pass, the nearest step, no S-curve); the default leaves the camera's
    profile as it is; 10 is the softest. Factors, so a hand-tuned profile keeps its proportions."""
    from trackxen.control.controller import Motion
    from trackxen.core.tracking_settings import manual_ramp, smoothness_motion

    assert smoothness_motion(Motion(), 5) == Motion()
    lively = smoothness_motion(Motion(), 1)
    assert (lively.ff_tau, lively.step_hysteresis, lively.jerk_time, lively.max_accel) == (0.0, 0.0, 0.0, 45.0)
    soft = smoothness_motion(Motion(), 10)
    assert (soft.ff_tau, soft.step_hysteresis, soft.jerk_time, soft.max_accel) == pytest.approx((0.4, 0.35, 0.3, 33.75))
    levels = [smoothness_motion(Motion(), s) for s in range(1, 11)]
    for a, b in zip(levels, levels[1:]):
        assert a.ff_tau < b.ff_tau and a.step_hysteresis < b.step_hysteresis
        assert a.jerk_time <= b.jerk_time and a.max_accel >= b.max_accel
        assert (a.gain, a.max_pan, a.lookahead, a.feedforward) == (b.gain, b.max_pan, b.lookahead, b.feedforward)
    tuned = smoothness_motion(Motion(max_accel=60.0, ff_tau=0.2), 10)
    assert tuned.max_accel == pytest.approx(45.0) and tuned.ff_tau == pytest.approx(0.2 * 0.4 / 0.15)
    assert manual_ramp(1) == 0.0 and manual_ramp(10) == 0.5
    assert all(manual_ramp(s) < manual_ramp(s + 1) for s in range(1, 10))

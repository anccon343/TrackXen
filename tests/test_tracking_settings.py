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

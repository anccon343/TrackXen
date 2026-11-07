"""The lens model: the exponential curve between the two ends, and a measured zoom table (control/lens.py)."""

import math

import pytest
from pydantic import ValidationError

from trackxen.control.lens import Lens
from trackxen.core.camera_models import MODELS
from trackxen.core.config import CameraConfig, LensConfig

G2_12X = MODELS["ptzoptics_g2_12x"]["lens"]["zoom_table"]  # measured on a PT12X-SDI-WH-G2


def table_lens(table) -> Lens:
    return Lens.from_config(LensConfig(zoom_table=table))


def half_tan(hfov: float) -> float:
    return math.tan(math.radians(hfov / 2))


def test_a_measured_table_is_followed_point_by_point():
    lens = table_lens(G2_12X)
    assert (lens.hfov_wide, lens.hfov_tele, lens.zoom_max) == (66.67, 6.31, 16384.0)
    for zoom, hfov in G2_12X:
        assert lens.hfov(zoom) == pytest.approx(hfov)
        assert lens.zoom_for_hfov(hfov) == pytest.approx(zoom)
    # the exponential curve through the same two ends is far off in the middle
    assert Lens(66.67, 6.31, 16384.0).hfov(8192) == pytest.approx(20.5, abs=0.1)
    assert lens.hfov(8192) == pytest.approx(28.01)


def test_between_points_the_focal_length_changes_evenly_on_a_log_scale():
    two = table_lens([[0, 60.0], [1000, 30.0]])
    assert half_tan(two.hfov(500)) == pytest.approx(math.sqrt(half_tan(60.0) * half_tan(30.0)))
    lens = table_lens(G2_12X)
    hfovs = [lens.hfov(z) for z in range(0, 16385, 97)]
    assert all(b < a for a, b in zip(hfovs, hfovs[1:]))  # narrower all the way in
    for z in (0, 1, 777, 5000, 10001, 16000, 16384):
        assert lens.zoom_for_hfov(lens.hfov(z)) == pytest.approx(z, abs=1e-6)


def test_outside_the_table_the_ends_hold():
    lens = table_lens(G2_12X)
    assert lens.hfov(-100) == 66.67 and lens.hfov(20000) == 6.31
    assert lens.zoom_for_hfov(90.0) == 0 and lens.zoom_for_hfov(1.0) == 16384.0
    assert lens.vfov(16384) < lens.hfov(16384) and lens.focal_px(1920, 0) < lens.focal_px(1920, 8192)


def test_without_a_table_the_curve_is_the_old_one():
    lens = Lens.from_config(LensConfig(hfov_wide=60.7, hfov_tele=3.36, zoom_max=16384))
    assert lens.zoom_table == () and lens.hfov(8192) == pytest.approx(math.sqrt(60.7 * 3.36))


@pytest.mark.parametrize("table", [
    [[0, 60]],  # one point
    [[100, 60], [200, 30]],  # not starting at zoom 0
    [[0, 60], [0, 30]],  # zoom not rising
    [[0, 60], [100, 70]],  # wider when zoomed in
    [[0, 200], [100, 30]],  # past 180 degrees
    [[0, 60], [100, 0]],  # no field of view
])
def test_a_wrong_zoom_table_is_refused(table):
    with pytest.raises(ValidationError):
        LensConfig(zoom_table=table)


def test_the_table_sets_the_lens_ends():
    cfg = LensConfig(hfov_wide=10, hfov_tele=1, zoom_max=5, zoom_table=[[0, 58.22], [15070, 3.65]])
    assert (cfg.hfov_wide, cfg.hfov_tele, cfg.zoom_max) == (58.22, 3.65, 15070)


def test_settings_saved_before_zoom_tables_still_load():
    cam = CameraConfig.model_validate({"lens": {"hfov_wide": 60.7, "hfov_tele": 3.36, "zoom_max": 16384}})
    assert cam.lens.zoom_table == [] and Lens.from_config(cam.lens).hfov(0) == pytest.approx(60.7)

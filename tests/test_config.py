import json

import pytest
from pydantic import ValidationError

from trackxen.core.config import AppConfig, CameraConfig, MixerConfig, VmixConfig, sim_config
from trackxen.core.tracking_settings import SLOTS


def refused(model, data) -> list[dict]:
    with pytest.raises(ValidationError) as e:
        model.model_validate(data)
    errors = e.value.errors(include_url=False)
    json.dumps(errors)  # POST /api/config answers 422 with these (not a 500)
    return errors


def test_camera_ids_must_be_unique():
    errors = refused(AppConfig, {"cameras": [{"id": "cam1", "name": "Pulpit"}, {"id": "cam1", "name": "Choir"}]})
    assert "cam1" in errors[0]["msg"]


def test_freed_ports_can_not_be_shared():
    refused(AppConfig, {"cameras": [{"id": "cam1", "freed_port": 40001}, {"id": "cam2", "freed_port": 40001}]})
    ok = AppConfig.model_validate({"cameras": [{"id": "cam1", "freed_port": None}, {"id": "cam2", "freed_port": None},
                                               {"id": "cam3", "freed_port": 40003}]})
    assert [c.freed_port for c in ok.cameras] == [None, None, 40003]
    assert sim_config(4, ptzoptics=1).cameras[3].freed_port is None


@pytest.mark.parametrize("model,data", [(VmixConfig, {"port": 0}), (VmixConfig, {"port": 70000}),
                                        (MixerConfig, {"port": 0}), (MixerConfig, {"port": 65536}),
                                        (CameraConfig, {"visca_port": 0}), (CameraConfig, {"freed_port": 0}),
                                        (CameraConfig, {"freed_port": 70000}), (AppConfig, {"web_port": 0})])
def test_ports_are_1_to_65535(model, data):
    refused(model, data)


def test_slots_are_always_five():
    assert len(CameraConfig.model_validate({"slots": []}).slots) == SLOTS
    cam = CameraConfig.model_validate({"slots": [{"name": f"S{i}"} for i in range(7)], "active_slot": 4})
    assert [s.name for s in cam.slots] == ["S0", "S1", "S2", "S3", "S4"] and cam.slot.name == "S4"


@pytest.mark.parametrize("data", [{"lens": {"zoom_max": 0}}, {"lens": {"hfov_tele": 0}},
                                  {"lens": {"hfov_wide": 3.0, "hfov_tele": 60.0}}, {"pan_speeds": []},
                                  {"tilt_speeds": [1.0, -2.0]}, {"units_per_degree": 0},
                                  {"tilt_units_per_degree": 0}, {"video_delay": float("nan")},
                                  {"motion": {"max_pan": float("inf")}}, {"motion": {"ff_tau": -0.1}},
                                  {"motion": {"ff_tau": 5.0}}, {"motion": {"jerk_time": 3.0}},
                                  {"motion": {"step_hysteresis": 0.8}}, {"motion": {"step_hysteresis": float("nan")}}])
def test_camera_calibration_values_that_would_crash_are_refused(data):
    refused(CameraConfig, data)


def test_saving_keeps_the_last_good_copy(tmp_path):
    path = tmp_path / "settings.json"
    AppConfig(web_port=8091).save(path)
    AppConfig(web_port=8092).save(path)
    assert AppConfig.load(path).web_port == 8092
    assert json.loads((tmp_path / "settings.json.bak").read_text())["web_port"] == 8091
    assert not (tmp_path / "settings.tmp").exists()


@pytest.mark.parametrize("content", [b"", b"\0" * 2048, b'{"cameras": [{"id": "cam1", "na', b"\xff\xfe garbage"])
def test_an_unreadable_settings_file_does_not_stop_trackxen(tmp_path, content, capsys):
    path = tmp_path / "settings.json"
    AppConfig(web_port=8091).save(path)
    AppConfig(web_port=8092).save(path)
    path.write_bytes(content)  # e.g. a power cut while Windows was writing it
    cfg = AppConfig.load(path)
    assert cfg.web_port == 8091  # the last good copy
    assert [p.read_bytes() for p in tmp_path.glob("settings.json.broken-*")] == [content]  # kept for a look
    assert "can't be read" in cfg.load_notes[0] and "can't be read" in capsys.readouterr().err
    assert AppConfig.load(path).web_port == 8091 and not AppConfig.load(path).load_notes  # settings.json is good again
    path.write_bytes(content)
    (tmp_path / "settings.json.bak").write_bytes(content)
    assert AppConfig.load(path).web_port == 8090  # no good copy either: the defaults


def test_text_saved_by_the_app_is_read_back(tmp_path):
    path = tmp_path / "settings.json"
    cfg = AppConfig(cameras=[CameraConfig(id="cam1", name="Pulpit \ud800", vmix_input="\udfff")])
    cfg.save(path)
    assert AppConfig.load(path).cameras[0].name == "Pulpit \ud800"


def test_values_no_longer_accepted_go_back_to_their_defaults_when_loading(tmp_path, capsys):
    """A settings file from an earlier version may hold values that are refused now: TrackXen still starts
    with everything else as it was."""
    path = tmp_path / "settings.json"
    cams = [CameraConfig(id="cam1", name="Pulpit", freed_port=40001, host="10.0.0.5").model_dump(),
            CameraConfig(id="cam1", name="Choir", freed_port=40001).model_dump(),
            CameraConfig(id="cam3", name="Wide").model_dump()]
    cams[0]["slots"][2]["composition"]["target_height"] = 0
    cams[1]["slots"][0]["initial"]["pan"] = float("nan")
    cams[2]["lens"]["zoom_max"] = 0
    data = {**AppConfig().model_dump(), "cameras": cams, "vmix": {"host": "10.0.0.9", "port": 0}, "web_port": 8095}
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = AppConfig.load(path)
    assert [c.id for c in cfg.cameras] == ["cam1", "cam2", "cam3"] and cfg.cameras[1].name == "Choir"
    assert [c.freed_port for c in cfg.cameras] == [40001, None, None]
    assert cfg.cameras[0].host == "10.0.0.5" and cfg.cameras[0].slots[2].composition.target_height == 0.55
    assert cfg.cameras[1].slots[0].initial.pan == 0.0 and cfg.cameras[2].lens.zoom_max == 16384.0
    assert cfg.vmix.host == "10.0.0.9" and cfg.vmix.port == 8099 and cfg.web_port == 8095
    assert not list(tmp_path.glob("settings.json.broken-*"))
    assert len(cfg.load_notes) == 6 and "'cam1'" in cfg.load_notes[-2] and "vmix.port" in capsys.readouterr().err


def test_settings_from_before_pan_tilt_smoothness_load_with_its_defaults(tmp_path):
    """A settings.json (or show file) saved before the smoothness fields existed: the defaults, nothing noted. A
    hand-edited value out of range goes back to its own default, the rest of the motion profile is kept."""
    path = tmp_path / "settings.json"
    data = AppConfig(cameras=[CameraConfig(id="cam1")]).model_dump()
    cam = data["cameras"][0]
    for slot in cam["slots"]:
        del slot["smoothness"]
    for key in ("ff_tau", "jerk_time", "step_hysteresis"):
        del cam["motion"][key]
    cam["motion"]["max_accel"] = 30.0
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = AppConfig.load(path)
    assert [s.smoothness for s in cfg.cameras[0].slots] == [5] * SLOTS and not cfg.load_notes
    assert cfg.cameras[0].motion.ff_tau == 0.15 and cfg.cameras[0].motion.max_accel == 30.0
    assert AppConfig.from_show(json.loads(path.read_text()), []).cameras[0].slots[0].smoothness == 5
    cam["motion"]["step_hysteresis"] = 3.0
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = AppConfig.load(path)
    assert cfg.cameras[0].motion.step_hysteresis == 0.25 and cfg.cameras[0].motion.max_accel == 30.0
    assert len(cfg.load_notes) == 1 and "step_hysteresis" in cfg.load_notes[0]
    cam["motion"]["step_hysteresis"] = 0.25
    cam["slots"][1]["smoothness"] = 11
    with pytest.raises(ValueError):  # a show file is checked like the settings page
        AppConfig.from_show(data, [])

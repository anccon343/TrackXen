from trackxen.core.performance import PRESETS, needed_images_per_s, recommend


def measured(light=0.0, normal=0.0, high=0.0, hyper=0.0) -> dict:
    return {("yolo26n-pose", 640): light, ("yolo26s-pose", 640): normal, ("yolo26m-pose", 640): high,
            ("yolo26m-pose", 960): hyper}


def test_needs():
    assert needed_images_per_s(PRESETS["light"], 4, 30) == 60  # 15/s per camera
    assert needed_images_per_s(PRESETS["normal"], 4, 30) == 120
    assert needed_images_per_s(PRESETS["hyper"], 4, 60) == 240  # every frame
    assert needed_images_per_s(PRESETS["normal"], 4, 25) == 100  # never more than the camera sends


def test_fast_pc_gets_hyper():
    rec = recommend(measured(900, 700, 500, 400), cameras=4)
    assert rec.mode == "hyper" and rec.auto_preset == "high"


def test_mid_pc_gets_auto_with_best_fitting_preset():
    rec = recommend(measured(400, 300, 150, 60), cameras=4)  # high needs 168 with 1.4x headroom
    assert rec.mode == "auto" and rec.auto_preset == "normal"
    assert "Recommended mode: AUTO" in rec.text()


def test_sharing_with_vmix_asks_more_headroom():
    assert recommend(measured(400, 300, 200, 60), cameras=4).auto_preset == "high"
    assert recommend(measured(400, 300, 200, 60), cameras=4, shared_with_vmix=True).auto_preset == "normal"


def test_slow_pc():
    assert recommend(measured(light=100), cameras=4).mode == "light"
    rec = recommend(measured(light=40), cameras=4)
    assert rec.mode == "insufficient" and "too slow" in rec.text()

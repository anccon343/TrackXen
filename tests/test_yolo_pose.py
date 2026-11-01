import importlib.util
from pathlib import Path

import numpy as np
import pytest

from trackxen.vision import yolo_pose

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolo26n-pose-b1.onnx"


def ultralytics_asset(name: str) -> Path | None:
    """Path to an image shipped with Ultralytics, found without importing it (and PyTorch)."""
    spec = importlib.util.find_spec("ultralytics")
    if not spec or not spec.submodule_search_locations:
        return None
    path = Path(list(spec.submodule_search_locations)[0]) / "assets" / name
    return path if path.exists() else None


def test_letterbox_geometry():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    blob, lb = yolo_pose.letterbox(frame, 640)
    assert blob.shape == (3, 640, 640) and blob.dtype == np.float32
    assert lb.scale == pytest.approx(1 / 3) and lb.pad_x == 0 and lb.pad_y == 140


def test_decode_end2end_maps_back_to_frame():
    lb = yolo_pose.Letterbox(scale=1 / 3, pad_x=0, pad_y=140)
    rows = np.zeros((300, 57), dtype=np.float32)
    rows[0, :6] = [100, 200, 160, 400, 0.9, 0]  # model coordinates
    rows[0, 6:9] = [130, 210, 0.95]  # nose
    rows[1, :6] = [0, 0, 10, 10, 0.1, 0]  # below the threshold
    people = yolo_pose.decode_end2end(rows, lb, conf=0.5)
    assert len(people) == 1
    p = people[0]
    np.testing.assert_allclose(p.box, [300, 180, 480, 780])
    np.testing.assert_allclose(p.keypoints[0], [390, 210, 0.95], rtol=1e-6)
    assert p.center == (390.0, 480.0)


def test_decode_raw_applies_nms():
    lb = yolo_pose.Letterbox(scale=1.0, pad_x=0, pad_y=0)
    pred = np.zeros((56, 3), dtype=np.float32)
    pred[:5, 0] = [100, 100, 50, 80, 0.9]  # cx, cy, w, h, score
    pred[:5, 1] = [102, 101, 50, 80, 0.8]  # nearly the same box
    pred[:5, 2] = [400, 300, 40, 90, 0.7]  # another person
    people = yolo_pose.decode_raw(pred, lb, conf=0.5)
    assert len(people) == 2
    assert sorted(round(p.score, 1) for p in people) == [0.7, 0.9]


@pytest.mark.skipif(not MODEL.exists() or not ultralytics_asset("bus.jpg"),
                    reason="run tools/env_check.py first to export the model")
def test_pose_detector_finds_people_on_cpu():
    import cv2

    image = cv2.imread(str(ultralytics_asset("bus.jpg")))
    detector = yolo_pose.PoseDetector(MODEL, engines=("cpu",), conf=0.5)
    people = detector.detect([image])[0]
    assert detector.engine == "cpu"
    assert len(people) >= 3
    h, w = image.shape[:2]
    for p in people:
        assert 0 <= p.center[0] <= w and 0 <= p.center[1] <= h

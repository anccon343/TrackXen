from pathlib import Path

import numpy as np
import pytest

from trackxen.core.hwtest import PEOPLE_PHOTO
from trackxen.vision import yolo_pose

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolo26n-pose-b1.onnx"


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


def fused_copy(tmp_path: Path) -> Path:
    """MODEL with the image preparation moved into it (tools/fuse_preprocess.py)."""
    import importlib.util
    import shutil

    pytest.importorskip("onnx")
    spec = importlib.util.spec_from_file_location("fuse_preprocess", MODEL.parents[1] / "tools" / "fuse_preprocess.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / "fused.onnx"
    shutil.copyfile(MODEL, path)
    assert tool.fuse(path) and not tool.fuse(path)  # the second time there's nothing to do
    return path


@pytest.mark.skipif(not MODEL.exists(), reason="run tools/export_models.py first to export the model")
def test_fused_model_finds_the_same_people(tmp_path):
    import cv2

    image = cv2.imread(str(PEOPLE_PHOTO))
    plain = yolo_pose.PoseDetector(MODEL, engines=("cpu",), conf=0.5)
    fused = yolo_pose.PoseDetector(fused_copy(tmp_path), engines=("cpu",), conf=0.5)
    assert fused.fused and not plain.fused and fused.size == plain.size == 640
    a, b = (sorted(d.detect([image])[0], key=lambda p: p.box[0]) for d in (plain, fused))
    assert len(a) == len(b) >= 3
    for p, q in zip(a, b):
        np.testing.assert_allclose(p.box, q.box, atol=1.0)
        np.testing.assert_allclose(p.keypoints[:, :2], q.keypoints[:, :2], atol=1.0)


@pytest.mark.skipif(not MODEL.exists(), reason="run tools/export_models.py first to export the model")
def test_reused_input_buffers_repaint_the_border_for_a_new_frame_size(tmp_path):
    detector = yolo_pose.PoseDetector(fused_copy(tmp_path), engines=("cpu",))
    wide = np.full((1080, 1920, 3), 200, np.uint8)
    square = np.full((480, 480, 3), 50, np.uint8)
    batches = [detector.prepare([wide]) for _ in range(detector.RING)]  # every buffer of the ring once
    assert len({id(b.data) for b in batches}) == detector.RING
    assert batches[0].data[0, 0, 0, 0] == 114 and batches[0].data[0, 320, 320, 0] == 200  # border, picture
    square_batch = detector.prepare([square])  # fills the first buffer completely
    assert square_batch.data is batches[0].data and square_batch.data[0, 0, 0, 0] == 50
    for _ in range(detector.RING - 1):
        detector.prepare([square])
    again = detector.prepare([wide])  # back in the first buffer: the border must be grey again
    assert again.data is batches[0].data and again.data[0, 0, 0, 0] == 114
    assert again.boxes[0] == batches[0].boxes[0]


@pytest.mark.skipif(not MODEL.exists(), reason="run tools/export_models.py first to export the model")
def test_pose_detector_finds_people_on_cpu():
    import cv2

    image = cv2.imread(str(PEOPLE_PHOTO))
    detector = yolo_pose.PoseDetector(MODEL, engines=("cpu",), conf=0.5)
    people = detector.detect([image])[0]
    assert detector.engine == "cpu"
    assert len(people) >= 3
    h, w = image.shape[:2]
    for p in people:
        assert 0 <= p.center[0] <= w and 0 <= p.center[1] <= h

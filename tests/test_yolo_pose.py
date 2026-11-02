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


def test_raw_export_output_goes_to_the_raw_decoder():
    detector = yolo_pose.PoseDetector.__new__(yolo_pose.PoseDetector)  # decode only: no model
    detector.conf = 0.5
    batch = yolo_pose.Batch(np.zeros(1), (yolo_pose.Letterbox(scale=1.0, pad_x=0, pad_y=0),), 1, 0.0)
    raw = np.zeros((1, 56, 8400), dtype=np.float32)  # (batch, 4 + 1 + 51, anchors)
    raw[0, :5, 100] = [320, 320, 50, 100, 0.9]
    people = detector.decode(batch, raw)[0]
    assert len(people) == 1
    np.testing.assert_allclose(people[0].box, [295, 270, 345, 370])
    end2end = np.zeros((1, 300, 57), dtype=np.float32)  # (batch, detections, 6 + 51)
    end2end[0, 0, :6] = [295, 270, 345, 370, 0.9, 0]
    people = detector.decode(batch, end2end)[0]
    assert len(people) == 1
    np.testing.assert_allclose(people[0].box, [295, 270, 345, 370])


def synthetic_model(path: Path, size: int = 640) -> Path:
    """A tiny stand-in for the exported YOLO26-pose model (same input and output shapes): one
    "person" around the red spot in the picture, nose at its centre. Bright red only: the grey
    padding (114) and other colours don't count, so channel order and scaling must be right."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    def const(name, value, dtype=np.float32):
        return numpy_helper.from_array(np.array(value, dtype), name)

    xs = (np.arange(size, dtype=np.float32) + 0.5).reshape(size, 1)
    inits = [const("xs", xs), const("sum_rc", [1, 2], np.int64), const("sum_cw", [1, 3], np.int64),
             const("sum_all", [1], np.int64), const("threshold", 0.6), const("eps", 1e-6), const("half", 20.0),
             const("score_cls", [[0.9, 0.0]]), const("rest", [[0.9] + [0.0] * 48]),
             const("empty", np.zeros((1, 299, 57))), const("one_row", [1, 1, 57], np.int64),
             const("r_start", [0], np.int64), const("r_end", [1], np.int64), const("r_axis", [1], np.int64)]
    node = helper.make_node
    nodes = [
        node("Slice", ["images", "r_start", "r_end", "r_axis"], ["red"]),  # channel 0 = R of RGB
        node("Sub", ["red", "threshold"], ["red_over"]), node("Relu", ["red_over"], ["spot"]),
        node("ReduceSum", ["spot", "sum_rc"], ["cols"], keepdims=0),  # (1, size): per column
        node("ReduceSum", ["spot", "sum_cw"], ["rows"], keepdims=0),  # (1, size): per row
        node("ReduceSum", ["cols", "sum_all"], ["mass"], keepdims=1), node("Add", ["mass", "eps"], ["total"]),
        node("MatMul", ["cols", "xs"], ["sx"]), node("Div", ["sx", "total"], ["cx"]),
        node("MatMul", ["rows", "xs"], ["sy"]), node("Div", ["sy", "total"], ["cy"]),
        node("Sub", ["cx", "half"], ["x1"]), node("Sub", ["cy", "half"], ["y1"]),
        node("Add", ["cx", "half"], ["x2"]), node("Add", ["cy", "half"], ["y2"]),
        node("Concat", ["x1", "y1", "x2", "y2", "score_cls", "cx", "cy", "rest"], ["first"], axis=1),
        node("Reshape", ["first", "one_row"], ["first_row"]),
        node("Concat", ["first_row", "empty"], ["output0"], axis=1),
    ]
    graph = helper.make_graph(nodes, "synthetic_pose",
                              [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
                              [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 300, 57])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=8)
    onnx.checker.check_model(model)
    onnx.save(model, str(path))
    return path


def fused_copy(tmp_path: Path, source: Path = MODEL) -> Path:
    """The model with the image preparation moved into it (tools/fuse_preprocess.py)."""
    import importlib.util
    import shutil

    pytest.importorskip("onnx")
    spec = importlib.util.spec_from_file_location("fuse_preprocess", Path(__file__).resolve().parents[1] / "tools" / "fuse_preprocess.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / "fused.onnx"
    shutil.copyfile(source, path)
    assert tool.fuse(path) and not tool.fuse(path)  # the second time there's nothing to do
    return path


def test_plain_and_fused_models_find_the_person_where_the_frame_has_them(tmp_path):
    import cv2

    plain_path = synthetic_model(tmp_path / "synthetic.onnx")
    plain = yolo_pose.PoseDetector(plain_path, engines=("cpu",), conf=0.5)
    fused = yolo_pose.PoseDetector(fused_copy(tmp_path, plain_path), engines=("cpu",), conf=0.5)
    assert fused.fused and not plain.fused and fused.size == plain.size == 640
    for x, y in [(1500, 300), (200, 900), (960, 540)]:  # the fused model's input buffers are reused
        frame = np.full((1080, 1920, 3), 60, np.uint8)
        frame[:, :, 0] = 255  # blue everywhere (dropped only with the right channel order)
        cv2.circle(frame, (x, y), 20, (0, 0, 255), -1)  # a red spot (BGR)
        for detector in (plain, fused):
            people = detector.detect([frame])[0]
            assert len(people) == 1
            np.testing.assert_allclose(people[0].keypoints[0, :2], [x, y], atol=3)
            np.testing.assert_allclose(people[0].center, [x, y], atol=3)


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


def test_reused_input_buffers_repaint_the_border_for_a_new_frame_size(tmp_path):
    # (only the model's input matters here: the stand-in has the real one's)
    detector = yolo_pose.PoseDetector(fused_copy(tmp_path, synthetic_model(tmp_path / "synthetic.onnx")), engines=("cpu",))
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

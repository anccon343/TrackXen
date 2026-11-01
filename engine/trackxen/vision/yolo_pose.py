"""People and body keypoints from a YOLO26-pose ONNX model.

Works with both export styles:
- end-to-end (exported with `nms=False`): output (batch, 300, 57) =
  x1, y1, x2, y2, score, class, then 17 keypoints x, y, confidence
- raw (default export): output (batch, 56, anchors) = cx, cy, w, h, score, 17 keypoints;
  overlapping boxes are then removed here with NMS.
Coordinates are mapped back from the 640x640 model input to the original frame.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from trackxen.vision.boxes import nms
from trackxen.vision.runtime import create_session

KEYPOINT_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
)


@dataclass
class Person:
    box: np.ndarray  # x1, y1, x2, y2 in frame pixels
    score: float
    keypoints: np.ndarray  # (17, 3): x, y, confidence in frame pixels

    @property
    def center(self) -> tuple[float, float]:
        return float((self.box[0] + self.box[2]) / 2), float((self.box[1] + self.box[3]) / 2)


@dataclass(frozen=True)
class Letterbox:
    scale: float
    pad_x: float
    pad_y: float


def letterbox(image: np.ndarray, size: int = 640) -> tuple[np.ndarray, Letterbox]:
    """Resize keeping proportions, pad to size x size; returns a CHW float32 RGB array.

    Per-channel scaling into a preallocated array measured fastest on 1080p frames
    (1.5 ms vs 1.8 ms for a transposed copy; cv2.dnn.blobFromImage was slower at 2.8 ms).
    """
    h, w = image.shape[:2]
    scale = min(size / h, size / w)
    nh, nw = round(h * scale), round(w * scale)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top:top + nh, left:left + nw] = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    out = np.empty((3, size, size), dtype=np.float32)
    for i, channel in enumerate((2, 1, 0)):  # BGR -> RGB
        np.multiply(canvas[:, :, channel], 1 / 255.0, out=out[i], dtype=np.float32)
    return out, Letterbox(scale, left, top)


def _unletterbox(xy: np.ndarray, lb: Letterbox) -> np.ndarray:
    out = xy.copy()
    out[..., 0] = (xy[..., 0] - lb.pad_x) / lb.scale
    out[..., 1] = (xy[..., 1] - lb.pad_y) / lb.scale
    return out


def decode_end2end(rows: np.ndarray, lb: Letterbox, conf: float, iou: float = 0.7) -> list[Person]:
    rows = rows[rows[:, 4] >= conf]
    if len(rows) > 1:  # duplicates of one person (see boxes.nms); 0.7 is Ultralytics' NMS default
        rows = rows[nms(rows[:, :4], rows[:, 4], iou)]
    people = []
    for r in rows:
        box = _unletterbox(r[:4].reshape(2, 2), lb).reshape(4)
        kpts = r[6:6 + 51].reshape(17, 3).copy()
        kpts[:, :2] = _unletterbox(kpts[:, :2], lb)
        people.append(Person(box, float(r[4]), kpts))
    return people


def decode_raw(pred: np.ndarray, lb: Letterbox, conf: float, iou: float = 0.5) -> list[Person]:
    rows = pred.T  # (anchors, 56)
    rows = rows[rows[:, 4] >= conf]
    if not len(rows):
        return []
    cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    boxes_xywh = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1)
    keep = cv2.dnn.NMSBoxes(boxes_xywh.tolist(), rows[:, 4].tolist(), conf, iou)
    people = []
    for i in np.array(keep).reshape(-1):
        x1, y1, bw, bh = boxes_xywh[i]
        box = _unletterbox(np.array([[x1, y1], [x1 + bw, y1 + bh]]), lb).reshape(4)
        kpts = rows[i, 5:5 + 51].reshape(17, 3).copy()
        kpts[:, :2] = _unletterbox(kpts[:, :2], lb)
        people.append(Person(box, float(rows[i, 4]), kpts))
    return people


class PoseDetector:
    """Runs the pose model on one or more frames at once (one batch per call)."""

    def __init__(self, model_path: str | Path, cache_dir: Path | None = None,
                 engines: tuple[str, ...] | None = None, conf: float = 0.4):
        kwargs = {"cache_dir": cache_dir}
        if engines:
            kwargs["engines"] = engines
        self.session, self.engine = create_session(model_path, **kwargs)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.batch = inp.shape[0] if isinstance(inp.shape[0], int) else None
        self.size = inp.shape[2] if isinstance(inp.shape[2], int) else 640
        self.conf = conf
        self.last_timing: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="letterbox")

    def detect(self, frames: list[np.ndarray]) -> list[list[Person]]:
        """People in each frame. `last_timing` holds the measured ms of each stage."""
        t0 = time.perf_counter()
        if len(frames) > 1:  # OpenCV releases the GIL: prepare the frames in parallel
            blobs, boxes = zip(*self._pool.map(lambda f: letterbox(f, self.size), frames))
        else:
            blobs, boxes = zip(*(letterbox(f, self.size) for f in frames))
        batch = np.stack(blobs)
        n = len(frames)
        if self.batch and self.batch > n:  # fixed-batch model: pad with blank frames
            batch = np.concatenate([batch, np.zeros((self.batch - n, *batch.shape[1:]), batch.dtype)])
        t1 = time.perf_counter()
        out = self.session.run(None, {self.input_name: batch})[0][:n]
        t2 = time.perf_counter()
        if out.shape[-1] == 57 or (out.ndim == 3 and out.shape[1] <= 300 and out.shape[2] > 6):
            people = [decode_end2end(out[i], boxes[i], self.conf) for i in range(n)]
        else:
            people = [decode_raw(out[i], boxes[i], self.conf) for i in range(n)]
        t3 = time.perf_counter()
        self.last_timing = {"pre_ms": (t1 - t0) * 1000, "infer_ms": (t2 - t1) * 1000, "post_ms": (t3 - t2) * 1000}
        return people

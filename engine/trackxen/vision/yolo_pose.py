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


@dataclass
class Batch:
    """Frames placed in a model's input, ready for the GPU (PoseDetector.prepare)."""

    data: np.ndarray  # the model input
    boxes: tuple[Letterbox, ...]  # where each frame went
    count: int  # real frames (a fixed-batch model has spare rows)
    pre_ms: float


class PoseDetector:
    """Runs the pose model on one or more frames at once (one batch per call).

    The three stages also run apart (vision/pipeline.py): `prepare` on the CPU, `infer` on the
    GPU (ONNX Runtime releases the GIL meanwhile) and `decode` on the CPU.

    Models patched by tools/fuse_preprocess.py take the letterboxed picture as uint8 and do the
    rest of the preparation on the GPU; the CPU then only resizes each frame into a reused input
    buffer. Plain models get the float input made here.
    """

    RING = 3  # input buffers per batch size: one on the GPU, one waiting for it, one being filled

    def __init__(self, model_path: str | Path, cache_dir: Path | None = None,
                 engines: tuple[str, ...] | None = None, conf: float = 0.4):
        kwargs = {"cache_dir": cache_dir}
        if engines:
            kwargs["engines"] = engines
        self.session, self.engine = create_session(model_path, **kwargs)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.fused = inp.type == "tensor(uint8)"  # (batch, size, size, 3) BGR, else (batch, 3, size, size) float
        size = inp.shape[1] if self.fused else inp.shape[2]
        self.batch = inp.shape[0] if isinstance(inp.shape[0], int) else None
        self.size = size if isinstance(size, int) else 640
        self.conf = conf
        self.last_timing: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="letterbox")
        self._ring: dict[int, list[np.ndarray]] = {}
        self._turn: dict[int, int] = {}
        self._painted: dict[tuple[int, int, int], tuple[int, int]] = {}  # (rows, buffer, row) -> frame size

    def prepare(self, frames: list[np.ndarray]) -> Batch:
        """Resize the frames into the model's input (CPU; OpenCV releases the GIL, so in parallel)."""
        t0 = time.perf_counter()
        n = len(frames)
        if self.fused:
            rows = self.batch or n
            if n > rows:
                raise ValueError(f"{n} frames for a model that takes {rows}")
            turn = self._turn.get(rows, 0)
            self._turn[rows] = (turn + 1) % self.RING
            ring = self._ring.setdefault(rows, [])
            if len(ring) <= turn:
                ring.append(np.full((rows, self.size, self.size, 3), 114, np.uint8))
            data = ring[turn]
            place = lambda i: self._place(data, (rows, turn, i), frames[i])  # noqa: E731
            boxes = tuple(self._pool.map(place, range(n))) if n > 1 else (place(0),)
        else:
            if n > 1:
                blobs, boxes = zip(*self._pool.map(lambda f: letterbox(f, self.size), frames))
            else:
                blobs, boxes = zip(*(letterbox(f, self.size) for f in frames))
            data = np.stack(blobs)
            if self.batch and self.batch > n:  # fixed-batch model: pad with blank frames
                data = np.concatenate([data, np.zeros((self.batch - n, *data.shape[1:]), data.dtype)])
        return Batch(data, tuple(boxes), n, (time.perf_counter() - t0) * 1000)

    def _place(self, data: np.ndarray, key: tuple[int, int, int], frame: np.ndarray) -> Letterbox:
        """One frame into its row of a reused input buffer; the grey border is painted only when
        the frame size changes."""
        h, w = frame.shape[:2]
        scale = min(self.size / h, self.size / w)
        nh, nw = round(h * scale), round(w * scale)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        row = data[key[2]]
        if self._painted.get(key) != (h, w):
            row[:] = 114
            self._painted[key] = (h, w)
        row[top:top + nh, left:left + nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        return Letterbox(scale, left, top)

    def infer(self, batch: Batch) -> np.ndarray:
        """The model (GPU). The batch's buffer must not be refilled until this returns."""
        return self.session.run(None, {self.input_name: batch.data})[0][:batch.count]

    def decode(self, batch: Batch, out: np.ndarray) -> list[list[Person]]:
        if out.shape[-1] == 57 or (out.ndim == 3 and out.shape[1] <= 300 and out.shape[2] > 6):
            return [decode_end2end(out[i], batch.boxes[i], self.conf) for i in range(batch.count)]
        return [decode_raw(out[i], batch.boxes[i], self.conf) for i in range(batch.count)]

    def detect(self, frames: list[np.ndarray]) -> list[list[Person]]:
        """People in each frame, the three stages in a row. `last_timing` holds the ms of each."""
        batch = self.prepare(frames)
        t1 = time.perf_counter()
        out = self.infer(batch)
        t2 = time.perf_counter()
        people = self.decode(batch, out)
        self.last_timing = {"pre_ms": batch.pre_ms, "infer_ms": (t2 - t1) * 1000,
                            "post_ms": (time.perf_counter() - t2) * 1000}
        return people

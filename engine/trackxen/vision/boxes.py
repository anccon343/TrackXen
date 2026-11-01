"""Box overlap helpers shared by the detector and the tracker (boxes as x1, y1, x2, y2)."""

from __future__ import annotations

import numpy as np


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between every box in a (N,4) and b (M,4)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    inter = _intersections(a, b)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def inside_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Share of each box in a (N,4) that lies inside each box in b (M,4)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    return _intersections(a, b) / np.maximum(area_a[:, None], 1e-9)


def _intersections(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    return np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Indices to keep, best first: a box is dropped when it overlaps a better one by more than `iou`.

    YOLO26's end-to-end head needs no NMS at its usual thresholds, but at the low threshold the
    tracker uses (0.1) it sometimes reports a second, nearly identical box for one person (IoU
    0.9+, measured in the simulator), which would become a duplicate track.
    """
    order = list(np.argsort(-scores))
    ious = iou_matrix(boxes, boxes)
    keep: list[int] = []
    for i in order:
        if all(ious[i, k] <= iou for k in keep):
            keep.append(int(i))
    return keep

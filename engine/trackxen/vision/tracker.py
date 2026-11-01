"""Keeps an ID on each person from frame to frame (ByteTrack-style).

Matching is by box overlap (IoU) in two stages: confident detections first, then
low-confidence ones, which keeps tracks alive through motion blur and partial
occlusion. Each track has a constant-velocity Kalman filter on box centre and size,
so it can be predicted through short gaps. Pure numpy: no PyTorch in the app.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from trackxen.vision.boxes import inside_matrix, iou_matrix
from trackxen.vision.yolo_pose import Person

__all__ = ["PersonTracker", "TrackOut", "greedy_match", "iou_matrix"]


def greedy_match(scores: np.ndarray, threshold: float) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Pairs (row, col) by descending score above threshold; returns matches and unmatched rows/cols."""
    matches, rows, cols = [], set(range(scores.shape[0])), set(range(scores.shape[1]))
    if scores.size:
        order = np.dstack(np.unravel_index(np.argsort(-scores, axis=None), scores.shape))[0]
        for r, c in order:
            if scores[r, c] < threshold:
                break
            if r in rows and c in cols:
                matches.append((int(r), int(c)))
                rows.discard(r)
                cols.discard(c)
    return matches, rows, cols


class BoxKalman:
    """Constant-velocity filter on [cx, cy, w, h] with time-varying dt (pixels, seconds)."""

    def __init__(self, box: np.ndarray):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = box[2] - box[0], box[3] - box[1]
        self.x = np.array([cx, cy, w, h, 0, 0, 0, 0], dtype=np.float64)
        s = max(h, 1.0)
        self.P = np.diag([(s / 10) ** 2] * 4 + [(s * 2) ** 2] * 4)

    def predict(self, dt: float) -> None:
        if dt <= 0:
            return
        F = np.eye(8)
        F[:4, 4:] = np.eye(4) * dt
        s = max(self.x[3], 1.0)
        q_pos, q_vel = (s / 20) ** 2 * dt, (s / 2) ** 2 * dt  # noise grows with box size and time
        Q = np.diag([q_pos] * 4 + [q_vel] * 4)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.x[2:4] = np.maximum(self.x[2:4], 1.0)

    def update(self, box: np.ndarray) -> None:
        z = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1]])
        H = np.zeros((4, 8))
        H[:, :4] = np.eye(4)
        s = max(z[3], 1.0)
        R = np.diag([(s / 20) ** 2] * 4)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (np.eye(8) - K @ H) @ self.P

    @property
    def box(self) -> np.ndarray:
        cx, cy, w, h = self.x[:4]
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[4]), float(self.x[5])

    def shift(self, dx: float, dy: float) -> None:
        self.x[0] += dx
        self.x[1] += dy


@dataclass
class Track:
    id: int
    kf: BoxKalman
    score: float
    keypoints: np.ndarray
    first_seen: float
    last_seen: float
    hits: int = 1
    missed: int = 0  # frames since the last matching detection
    confirmed: bool = False


@dataclass
class TrackOut:
    """What a frame's tracking result says about one person (pixels of that frame)."""

    id: int
    box: list[float]  # x1, y1, x2, y2
    score: float
    keypoints: list[list[float]]  # 17 x (x, y, confidence)
    velocity: tuple[float, float]  # pixels per second
    missed: int  # 0 = seen in this frame; >0 = predicted
    age: float  # seconds since first seen

    def to_dict(self) -> dict:
        return {"id": self.id, "box": [round(v, 1) for v in self.box], "score": round(self.score, 3),
                "keypoints": [[round(x, 1), round(y, 1), round(c, 2)] for x, y, c in self.keypoints],
                "velocity": [round(self.velocity[0], 1), round(self.velocity[1], 1)],
                "missed": self.missed, "age": round(self.age, 2)}


@dataclass
class PersonTracker:
    # Thresholds follow ByteTrack / Ultralytics practice (high ~0.25, low 0.1, first match IoU 0.2).
    # People at a podium, half hidden or backlit often score only 0.3-0.5, and must still be tracked.
    high_score: float = 0.3  # detections at least this sure are matched first
    low_score: float = 0.1  # weaker detections only extend existing tracks
    new_track_score: float = 0.35  # plus confirm_hits frames in a row before a track is reported
    match_iou: float = 0.2
    low_match_iou: float = 0.4
    max_missing: float = 1.0  # seconds a track survives without detections
    confirm_hits: int = 2
    _tracks: list[Track] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _last_t: float | None = None

    @staticmethod
    def _part_of(box: np.ndarray, people: list[np.ndarray]) -> bool:
        """Mostly inside one of these boxes and less than half its size?"""
        if not people:
            return False
        others = np.array(people).reshape(-1, 4)
        area = (box[2] - box[0]) * (box[3] - box[1])
        areas = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
        return bool(np.any((inside_matrix(box[None], others)[0] > 0.8) & (area < 0.5 * areas)))

    def compensate(self, dx: float, dy: float) -> None:
        """Shift every track by the image motion the camera caused (pixels)."""
        for t in self._tracks:
            t.kf.shift(dx, dy)

    def update(self, people: list[Person], t: float) -> list[TrackOut]:
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        for tr in self._tracks:
            tr.kf.predict(dt)

        dets = [p for p in people if p.score >= self.low_score]
        high = [i for i, p in enumerate(dets) if p.score >= self.high_score]
        low = [i for i, p in enumerate(dets) if p.score < self.high_score]
        det_boxes = np.array([p.box for p in dets], dtype=np.float64).reshape(-1, 4)
        track_boxes = np.array([tr.kf.box for tr in self._tracks]).reshape(-1, 4)

        # Stage 1: confident detections against all tracks
        ious = iou_matrix(track_boxes, det_boxes[high])
        matches, free_tracks, free_high = greedy_match(ious, self.match_iou)
        matched = [(ti, high[di]) for ti, di in matches]
        # Stage 2: weak detections against the tracks still unmatched
        free_list = sorted(free_tracks)
        ious_low = iou_matrix(track_boxes[free_list], det_boxes[low])
        low_matches, _, _ = greedy_match(ious_low, self.low_match_iou)
        matched += [(free_list[ti], low[di]) for ti, di in low_matches]

        matched_tracks = set()
        for ti, di in matched:
            tr, det = self._tracks[ti], dets[di]
            tr.kf.update(det.box)
            tr.score, tr.keypoints, tr.last_seen = det.score, det.keypoints, t
            tr.hits += 1
            tr.missed = 0
            tr.confirmed = tr.confirmed or tr.hits >= self.confirm_hits
            matched_tracks.add(ti)
        for ti, tr in enumerate(self._tracks):
            if ti not in matched_tracks:
                tr.missed += 1
        self._tracks = [tr for tr in self._tracks if t - tr.last_seen <= self.max_missing
                        and (tr.confirmed or tr.missed == 0)]

        # New tracks from sure detections no track took, except a box mostly inside a person seen
        # in this frame and much smaller than them: a part of someone (an arm, a sliver at an edge)
        seen = [tr.kf.box for tr in self._tracks if tr.missed == 0]
        for det in sorted((dets[high[di]] for di in free_high), key=lambda d: -d.score):
            if det.score < self.new_track_score or self._part_of(det.box, seen):
                continue
            self._tracks.append(Track(next(self._ids), BoxKalman(det.box), det.score, det.keypoints, t, t))
            seen.append(det.box)

        # Reported boxes are the filtered ones: smooth, and predicted while a person is briefly missed
        return [TrackOut(tr.id, tr.kf.box.tolist(), tr.score, tr.keypoints.tolist(), tr.kf.velocity,
                         tr.missed, t - tr.first_seen)
                for tr in self._tracks if tr.confirmed]

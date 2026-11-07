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

    # made once, not on every call (a wide shot can hold a hundred people)
    I8 = np.eye(8)
    H = np.eye(4, 8)  # measures [cx, cy, w, h]

    def __init__(self, box: np.ndarray):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = box[2] - box[0], box[3] - box[1]
        self.x = np.array([cx, cy, w, h, 0, 0, 0, 0], dtype=np.float64)
        s = max(h, 1.0)
        self.P = np.diag([(s / 10) ** 2] * 4 + [(s * 2) ** 2] * 4)

    def predict(self, dt: float) -> None:
        if dt <= 0:
            return
        F = self.I8.copy()
        F[:4, 4:] = self.I8[:4, :4] * dt
        s = max(self.x[3], 1.0)
        q_pos, q_vel = (s / 20) ** 2 * dt, (s / 2) ** 2 * dt  # noise grows with box size and time
        Q = np.diag([q_pos] * 4 + [q_vel] * 4)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.x[2:4] = np.maximum(self.x[2:4], 1.0)

    def update(self, box: np.ndarray) -> None:
        z = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1]])
        H = self.H
        s = max(z[3], 1.0)
        R = np.diag([(s / 20) ** 2] * 4)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (self.I8 - K @ H) @ self.P

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

    def scale(self, sx: float, sy: float) -> None:
        """Into the pixels of a picture of another size (x values times sx, y values times sy)."""
        d = np.array([sx, sy] * 4)
        self.x = self.x * d
        self.P = self.P * np.outer(d, d)


FACE_SURE = 0.5  # face keypoints at least this sure count as a seen head


def _own_head(det: Person, keypoints: np.ndarray) -> bool:
    """Does this detection show a head of its own, not the one at these keypoints (a person's)?

    A second, smaller box of one person carries that person's face keypoints. Another person
    inside their box (a speaker in front of a screen showing a close-up) has a face elsewhere,
    or a much smaller one."""
    sure = det.keypoints[:5, 2] >= FACE_SURE
    if sure.sum() < 2:
        return False  # no face seen: an arm, a sliver, someone's back
    mine, theirs = det.keypoints[:5][sure, :2], keypoints[:5][sure, :2]  # the same face points
    size = max(float(np.ptp(mine, axis=0).max()), (det.box[2] - det.box[0]) * 0.1, 1.0)
    their_size = float(np.ptp(theirs, axis=0).max())
    apart = float(np.linalg.norm(mine - theirs, axis=1).mean())
    return apart > size or their_size > 2 * size


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
    low_score: float = 0.1  # weaker detections extend existing tracks (start one only if new_track_score is lower)
    new_track_score: float = 0.35  # plus confirm_hits frames in a row before a track is reported
    match_iou: float = 0.2
    low_match_iou: float = 0.4
    max_missing: float = 1.0  # seconds a track survives without detections
    confirm_hits: int = 2
    _tracks: list[Track] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _last_t: float | None = None

    @staticmethod
    def _part_of(det: Person, people: list[tuple[np.ndarray, np.ndarray]]) -> bool:
        """Mostly inside one of these people's boxes (box, keypoints) and less than half its size,
        without a head of its own?"""
        if not people:
            return False
        box = det.box
        others = np.array([b for b, _ in people]).reshape(-1, 4)
        area = (box[2] - box[0]) * (box[3] - box[1])
        areas = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
        inside = (inside_matrix(box[None], others)[0] > 0.8) & (area < 0.5 * areas)
        return any(not _own_head(det, people[i][1]) for i in np.flatnonzero(inside))

    def rescale(self, sx: float, sy: float) -> None:
        """The source's picture size changed: move every track into the new picture's pixels."""
        for t in self._tracks:
            t.kf.scale(sx, sy)
            t.keypoints = t.keypoints * np.array([sx, sy, 1.0])

    def compensate(self, dx: float, dy: float) -> None:
        """Shift every track by the image motion the camera caused (pixels)."""
        for t in self._tracks:
            t.kf.shift(dx, dy)

    def update(self, people: list[Person], t: float) -> list[TrackOut]:
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        for tr in self._tracks:
            tr.kf.predict(dt)

        # (a sure detection counts even when "keep" is set above "sure")
        dets = [p for p in people if p.score >= min(self.low_score, self.high_score)]
        high = [i for i, p in enumerate(dets) if p.score >= self.high_score]
        low = [i for i, p in enumerate(dets) if p.score < self.high_score]
        det_boxes = np.array([p.box for p in dets], dtype=np.float64).reshape(-1, 4)
        track_boxes = np.array([tr.kf.box for tr in self._tracks]).reshape(-1, 4)

        # Stage 1: confident detections against all tracks
        ious = iou_matrix(track_boxes, det_boxes[high])
        matches, free_tracks, free_high = greedy_match(ious, self.match_iou)
        matched = [(ti, high[di]) for ti, di in matches]
        # Stage 2: weak detections against the tracks still unmatched (never with less overlap than
        # stage 1 needs: a weak detection must not match more easily than a sure one)
        free_list = sorted(free_tracks)
        ious_low = iou_matrix(track_boxes[free_list], det_boxes[low])
        low_matches, _, free_low = greedy_match(ious_low, max(self.match_iou, self.low_match_iou))
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

        # New tracks from detections no track took that are at least new_track_score sure (with
        # "start" set below "sure", weak ones too), except a box mostly inside a person seen in this
        # frame and much smaller than them: a part of someone (an arm, a sliver at an edge), unless
        # it has a head of its own (a speaker in front of a big screen showing a person)
        seen = [(tr.kf.box, tr.keypoints) for tr in self._tracks if tr.missed == 0]
        free = [dets[high[di]] for di in free_high] + [dets[low[di]] for di in free_low]
        for det in sorted(free, key=lambda d: -d.score):
            if det.score < self.new_track_score or self._part_of(det, seen):
                continue
            self._tracks.append(Track(next(self._ids), BoxKalman(det.box), det.score, det.keypoints, t, t,
                                      confirmed=self.confirm_hits <= 1))
            seen.append((det.box, det.keypoints))

        # Reported boxes are the filtered ones: smooth, and predicted while a person is briefly missed
        return [TrackOut(tr.id, tr.kf.box.tolist(), tr.score, tr.keypoints.tolist(), tr.kf.velocity,
                         tr.missed, t - tr.first_seen)
                for tr in self._tracks if tr.confirmed]

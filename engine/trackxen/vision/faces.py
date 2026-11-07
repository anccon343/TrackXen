"""Registered Face Tracking: who is who, by face.

OpenCV's YuNet finds the face (and 5 landmarks) inside a tracked person's head crop; SFace turns
the aligned face into a 128-number fingerprint; fingerprints are compared by cosine similarity
(0.363 and above = the same person, OpenCV's published threshold for SFace).

Runs in its own thread inside the vision worker, on small head crops, so detection never waits
for it: each person is looked at about twice a second. Face data never leaves this PC.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"
SAME_PERSON = 0.363  # cosine similarity
RECHECK_S = 0.5  # how often each person's face is looked at
MIN_FACE_PX = 24  # smaller faces are not compared (too little detail)
GUIDE = 0.34  # registration frame: a centred square this share of the picture height
# A face registered from a chosen person (not the centred frame) must be at least this share of the picture
# height wide: the size the centred frame asks for would be 0.34 x 0.35 = 0.12, which needs a close-up.
MIN_PICKED_FACE = 0.06


def available(models: Path) -> bool:
    return (models / YUNET).exists() and (models / SFACE).exists()


@dataclass
class Job:
    kind: str  # "identify" or "guide"
    camera: str
    crop: np.ndarray  # BGR
    track_id: int = 0
    capture: int = 0  # guide jobs: a capture request number (0 = only check the quality)
    frame: tuple[float, float, float, float] | None = None  # guide jobs on a chosen person: the crop in the picture, 0..1
    min_face: float = 0.0  # guide jobs on a chosen person: the smallest usable face width, pixels
    near: tuple[float, float, float] | None = None  # identify jobs: the person's head in the crop (head_view)


def head_square(shape: tuple[int, ...], box: list[float],
                keypoints: list[list[float]]) -> tuple[int, int, int, int, float, float, float] | None:
    """A square around the head: from the face keypoints when seen, else the top of the box. Returns its
    left, top, right, bottom in the picture (cut at the picture's edge) and the head's centre and the square's
    side before cutting; None when too small."""
    h, w = shape[:2]
    face = [(x, y) for x, y, c in keypoints[:5] if c >= 0.3]
    x1, y1, x2, y2 = box
    if len(face) >= 2:
        xs, ys = [p[0] for p in face], [p[1] for p in face]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        size = max(max(xs) - min(xs), max(ys) - min(ys), (x2 - x1) * 0.25) * 2.4
    else:
        cx, cy, size = (x1 + x2) / 2, y1 + (x2 - x1) * 0.35, (x2 - x1) * 0.9
    half = size / 2
    left, top = int(max(0, cx - half)), int(max(0, cy - half))
    right, bottom = int(min(w, cx + half)), int(min(h, cy + half))
    if right - left < MIN_FACE_PX or bottom - top < MIN_FACE_PX:
        return None
    return left, top, right, bottom, cx, cy, size


def head_view(image: np.ndarray, box: list[float],
              keypoints: list[list[float]]) -> tuple[np.ndarray, tuple[float, float, float]] | None:
    """The head_square's crop, and where the head is in it: x, y of its centre (off the crop's centre when
    the square was cut at the picture's edge) and the square's side. None when too small."""
    sq = head_square(image.shape, box, keypoints)
    if sq is None:
        return None
    left, top, right, bottom, cx, cy, size = sq
    return image[top:bottom, left:right].copy(), (cx - left, cy - top, size)


def head_crop(image: np.ndarray, box: list[float], keypoints: list[list[float]]) -> np.ndarray | None:
    """Just the crop of head_view."""
    view = head_view(image, box, keypoints)
    return None if view is None else view[0]


def guide_crop(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    size = int(h * GUIDE)
    left, top = (w - size) // 2, (h - size) // 2
    return image[top:top + size, left:left + size].copy()


class FaceEngine:
    """YuNet + SFace on one thread. Not thread-safe: use it from one thread only."""

    def __init__(self, models: Path):
        self.detector = cv2.FaceDetectorYN.create(str(models / YUNET), "", (160, 160), 0.7, 0.3, 20)
        self.recognizer = cv2.FaceRecognizerSF.create(str(models / SFACE), "")

    def find(self, crop: np.ndarray, near: tuple[float, float, float] | None = None) -> np.ndarray | None:
        """The biggest face in the crop (YuNet row: box, 5 landmarks, score), small crops enlarged.

        With `near` (x, y, size: where a person's head is in their head crop), the face nearest
        that point instead, and none if even that one is more than a quarter of `size` away: a
        neighbour's face at the edge of the crop of someone facing away is not theirs."""
        scale = 2.0 if max(crop.shape[:2]) < 160 else 1.0
        img = cv2.resize(crop, None, fx=scale, fy=scale) if scale != 1.0 else crop
        self.detector.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self.detector.detect(img)
        if faces is None or not len(faces):
            return None
        faces = np.array(faces, dtype=np.float32)
        faces[:, :14] /= scale
        if near is None:
            return max(faces, key=lambda f: f[2] * f[3])
        x, y, size = near
        off = np.hypot(faces[:, 0] + faces[:, 2] / 2 - x, faces[:, 1] + faces[:, 3] / 2 - y)
        nearest = int(np.argmin(off))
        return faces[nearest] if off[nearest] <= size * 0.25 else None

    def fingerprint(self, crop: np.ndarray, face: np.ndarray) -> np.ndarray:
        feature = self.recognizer.feature(self.recognizer.alignCrop(crop, face)).reshape(-1)
        return feature / max(np.linalg.norm(feature), 1e-9)

    def quality(self, crop: np.ndarray, face: np.ndarray | None) -> str:
        """Why this face isn't good enough to register ("" = good): rules of thumb."""
        if face is None:
            return "no face in the frame"
        x, y, w, h = face[:4]
        size = crop.shape[0]
        if face[14] < 0.85:
            return "face not clear"
        if w < size * 0.35:
            return "too small: come closer or zoom in"
        if w > size * 0.95:
            return "too large: step back or zoom out"
        cx, cy = x + w / 2, y + h / 2
        if abs(cx - size / 2) > size * 0.18 or abs(cy - size / 2) > size * 0.18:
            return "centre the face in the frame"
        (rx, ry), (lx, ly), (nx, _) = face[4:6], face[6:8], face[8:10]
        eyes = max(abs(lx - rx), 1.0)
        if abs(nx - (rx + lx) / 2) / eyes > 0.18 or abs(ly - ry) / eyes > 0.25:
            return "face the camera"
        x0, y0 = int(max(0, x)), int(max(0, y))
        gray = cv2.cvtColor(crop[y0:int(y + h), x0:int(x + w)], cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean()) if gray.size else 0.0
        if brightness < 50:
            return "too dark"
        if brightness > 210:
            return "too bright"
        return ""


class FaceWorker:
    """The face thread: takes crops from the detection loop, gives back identities and registrations."""

    def __init__(self, models: Path, emit):
        self.models, self.emit = models, emit
        self.gallery: list[tuple[str, np.ndarray]] = []  # (face id, fingerprint)
        self.jobs: queue.Queue[Job] = queue.Queue(maxsize=16)
        self.identities: dict[tuple[str, int], tuple[str, float, float]] = {}  # (camera, track) -> id, score, when
        self.checked: dict[tuple[str, int], float] = {}
        self.guides: dict[str, int] = {}  # camera -> pending capture number (0 = only checking)
        self.picks: dict[str, int] = {}  # camera -> the person (track id) to register; none: the centred frame
        self.lock = threading.Lock()  # for `guides`: the face thread takes a capture, the loop opens and closes
        self.failed = False  # the models couldn't be loaded: the thread has ended
        self._last_guide: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, name="faces", daemon=True)
        self._thread.start()

    def set_gallery(self, faces: list[dict]) -> None:
        self.gallery = [(f["id"], np.asarray(f["feature"], dtype=np.float32)) for f in faces]
        self.identities.clear()  # names may have changed: look again

    def submit(self, camera: str, image: np.ndarray, tracks: list, now: float) -> None:
        """Called by the detection loop after each frame: queue the faces that are due a look."""
        if self.failed:
            return
        if camera in self.guides and now - self._last_guide.get(camera, 0.0) >= 0.2:
            self._last_guide[camera] = now
            self._guide_job(camera, image, tracks)
        if not self.gallery:
            return
        alive = {(camera, t.id) for t in tracks}
        for key in [k for k in self.checked if k[0] == camera and k not in alive]:
            self.checked.pop(key, None)
            self.identities.pop(key, None)
        due = sorted((t for t in tracks if t.missed == 0 and now - self.checked.get((camera, t.id), 0.0) >= RECHECK_S),
                     key=lambda t: self.checked.get((camera, t.id), 0.0))
        for t in due[:2]:  # at most two people per frame and camera
            view = head_view(image, t.box, t.keypoints)
            self.checked[(camera, t.id)] = now
            if view is not None:
                self._put(Job("identify", camera, view[0], track_id=t.id, near=view[1]))

    def _guide_job(self, camera: str, image: np.ndarray, tracks: list) -> None:
        """The registration frame: centred, or around the head of the person chosen in the picture."""
        capture = self.guides.get(camera, 0)
        pick = self.picks.get(camera)
        if pick is None:
            self._put(Job("guide", camera, guide_crop(image), capture=capture))
            return
        h, w = image.shape[:2]
        person = next((t for t in tracks if t.id == pick and t.missed == 0), None)
        sq = None if person is None else head_square(image.shape, person.box, person.keypoints)
        if sq is None:
            self.emit({"type": "face_guide", "camera": camera, "ok": False, "box": None, "frame": None,
                       "problem": "the chosen person is not in the picture: click them again" if person is None
                       else "too small: zoom in"})
            return
        left, top, right, bottom = sq[:4]
        frame = (round(left / w, 4), round(top / h, 4), round((right - left) / w, 4), round((bottom - top) / h, 4))
        self._put(Job("guide", camera, image[top:bottom, left:right].copy(), capture=capture, frame=frame,
                      min_face=MIN_PICKED_FACE * h))

    def identity(self, camera: str, track_id: int) -> dict | None:
        found = self.identities.get((camera, track_id))
        return None if found is None else {"id": found[0], "score": round(found[1], 3)}

    def _put(self, job: Job) -> None:
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            pass  # the face thread is busy: these people are looked at next time

    def _run(self) -> None:
        try:
            engine = FaceEngine(self.models)
        except Exception as e:  # noqa: BLE001 - e.g. a damaged model file: say so (the next face command tries again)
            self.failed = True
            self.emit({"type": "error", "message": f"face recognition unavailable: {str(e).strip().splitlines()[0][:300]}"})
            return
        while True:
            job = self.jobs.get()
            try:
                if job.kind == "identify":
                    self._identify(engine, job)
                else:
                    self._guide(engine, job)
            except Exception as e:  # noqa: BLE001 - one bad crop must not stop face recognition
                self.emit({"type": "error", "camera": job.camera, "message": f"face recognition: {e}"})

    def _identify(self, engine: FaceEngine, job: Job) -> None:
        face = engine.find(job.crop, job.near)
        if face is None or face[2] < MIN_FACE_PX or not self.gallery:
            return
        mine = engine.fingerprint(job.crop, face)
        scores = [(float(np.dot(mine, feature)), fid) for fid, feature in self.gallery]
        score, fid = max(scores)
        key = (job.camera, job.track_id)
        if score >= SAME_PERSON:
            self.identities[key] = (fid, score, time.monotonic())
        elif key in self.identities and score < SAME_PERSON * 0.6:
            self.identities.pop(key)  # clearly someone else after all

    def _guide(self, engine: FaceEngine, job: Job) -> None:
        face = engine.find(job.crop)
        problem = engine.quality(job.crop, face)
        if job.frame is not None:  # a chosen person: the frame follows their head
            if face is not None and face[2] < job.min_face and problem in ("", "too large: step back or zoom out"):
                problem = "too small: zoom in"
            elif problem == "centre the face in the frame":
                problem = "too near the picture's edge: move the camera"
        box = None if face is None else [round(float(v) / job.crop.shape[0], 3) for v in face[:4]]
        self.emit({"type": "face_guide", "camera": job.camera, "ok": not problem, "problem": problem, "box": box,
                   "frame": list(job.frame) if job.frame else None})
        if not job.capture or problem:
            return
        with self.lock:  # take the capture first: a frame closed meanwhile stays closed
            if self.guides.get(job.camera) != job.capture:
                return
            self.guides[job.camera] = 0
        x, y, w, h = (int(v) for v in face[:4])
        pad = int(w * 0.3)
        thumb = job.crop[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        thumb = cv2.resize(thumb, (96, 96 * thumb.shape[0] // max(thumb.shape[1], 1)))
        ok, jpeg = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self.emit({"type": "face_captured", "camera": job.camera, "capture": job.capture,
                   "feature": engine.fingerprint(job.crop, face).tolist(), "jpeg": jpeg.tobytes() if ok else b""})

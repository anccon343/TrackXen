import time
from pathlib import Path

import numpy as np
import pytest

from trackxen.core.config import CameraConfig, FaceRef
from trackxen.core.faces import FaceStore
from trackxen.core.session import CameraSession
from trackxen.vision import faces

from test_session import FakeLink, Log, message, track

MODELS = Path(__file__).resolve().parent.parent / "models"


def face_track(tid, cx, face_id=None, age=5.0):
    t = track(tid, cx, age=age)
    if face_id:
        t["face"] = {"id": face_id, "score": 0.7}
    return t


def faces_session(**slot):
    log = Log()
    cfg = CameraConfig(id="cam1", video_delay=0.0, faces=[FaceRef(id="pastor"), FaceRef(id="leader")])
    s = CameraSession(cfg, FakeLink(), log=log)
    s.update_slot({"registered_faces": True, **slot})
    s.face_names = {"pastor": "Pastor", "leader": "Worship leader"}
    return s, log


def test_registered_face_with_the_highest_priority_is_picked_at_once():
    s, log = faces_session()
    s.set_tracking(True)
    s.on_tracks(message([face_track(1, 500), face_track(2, 960, "leader"), face_track(3, 1400, "pastor")]))
    assert s.target_id == 3 and log.said("registered face: Pastor")


def test_unregistered_or_switched_off_faces_are_not_picked():
    s, _ = faces_session()
    s.cfg.faces[0].enabled = False  # the pastor is off for this camera
    s.set_tracking(True)
    s.on_tracks(message([face_track(1, 500), face_track(3, 1400, "pastor")]))
    assert s.target_id is None


def test_a_higher_priority_face_does_not_take_over():
    s, _ = faces_session()
    s.set_tracking(True)
    s.on_tracks(message([face_track(2, 960, "leader")]))
    assert s.target_id == 2
    s.on_tracks(message([face_track(2, 960, "leader"), face_track(3, 1400, "pastor")]))
    assert s.target_id == 2  # Canon: once locked, it stays


def test_lost_target_is_re_found_by_face_anywhere_in_the_picture():
    s, log = faces_session(registered_faces=False)
    s.on_tracks(message([face_track(2, 400, "leader")]))
    s.select_id(2)
    s.on_tracks(message([face_track(2, 400, "leader")]))
    assert s.target_face == "leader"
    s.on_tracks(message([face_track(9, 1700, "leader", age=0.2)]))  # far from where they were last seen
    assert s.target_id == 9 and log.said("re-found by face")


def test_someone_known_to_be_another_person_is_never_adopted():
    s, _ = faces_session(registered_faces=False)
    s.on_tracks(message([face_track(2, 960, "leader")]))
    s.select_id(2)
    s.on_tracks(message([face_track(2, 960, "leader")]))
    s.on_tracks(message([face_track(9, 980, "pastor", age=0.2)]))  # right where the target was, but the pastor
    assert s.target_id == 2


def test_face_store_keeps_faces_on_disk_and_wipes_them(tmp_path):
    store = FaceStore(tmp_path / "faces")
    face = store.add("Pastor", [0.1] * 128, b"\xff\xd8jpeg")
    again = FaceStore(tmp_path / "faces")
    assert again.faces[face.id].name == "Pastor" and again.thumbnail(face.id) == b"\xff\xd8jpeg"
    again.wipe()
    assert not (tmp_path / "faces").exists() and FaceStore(tmp_path / "faces").faces == {}


def test_head_crop_follows_the_face_keypoints():
    image = np.zeros((1080, 1920, 3), np.uint8)
    kp = [[1000, 300, 0.9], [990, 290, 0.9], [1010, 290, 0.9], [980, 295, 0.9], [1020, 295, 0.9]] + [[0, 0, 0]] * 12
    crop = faces.head_crop(image, [940, 250, 1060, 700], kp)
    assert crop is not None and 60 <= crop.shape[0] <= 130 and crop.shape[0] == crop.shape[1]


def face_row(x, y, w, h, score=0.95):
    """A YuNet result row: box, right eye, left eye, nose, mouth corners, score (a frontal face)."""
    return [x, y, w, h, x + 0.3 * w, y + 0.4 * h, x + 0.7 * w, y + 0.4 * h, x + 0.5 * w, y + 0.6 * h,
            x + 0.35 * w, y + 0.8 * h, x + 0.65 * w, y + 0.8 * h, score]


class FakeYuNet:
    """cv2.FaceDetectorYN's interface: always finds these faces (crops of 160 px and more: not enlarged)."""

    def __init__(self, rows):
        self.rows = np.array(rows, np.float32).reshape(-1, 15)

    def setInputSize(self, size):
        pass

    def detect(self, img):
        return 1, (self.rows.copy() if len(self.rows) else None)


class FakeSFace:
    """cv2.FaceRecognizerSF's interface: each face's fingerprint is looked up by its x in the crop."""

    def __init__(self, prints):
        self.prints = prints

    def alignCrop(self, crop, face):
        return face

    def feature(self, face):
        return np.asarray(self.prints.get(int(round(face[0])), [0.0] * 128), np.float32).reshape(1, -1)


def one_hot(i):
    v = [0.0] * 128
    v[i] = 1.0
    return v


FaceEngine = faces.FaceEngine


def face_worker(monkeypatch, rows, prints=None):
    engine = FaceEngine.__new__(FaceEngine)  # YuNet and SFace stand-ins, the real logic
    engine.detector, engine.recognizer = FakeYuNet(rows), FakeSFace(prints or {})
    monkeypatch.setattr(faces, "FaceEngine", lambda models: engine)
    sent = []
    return sent, faces.FaceWorker(MODELS, sent.append), engine


def settle(worker, sent):
    """Wait until the face thread has done every job queued so far."""
    done = sum(m["type"] == "face_guide" for m in sent)
    worker._put(faces.Job("guide", "sync", np.full((200, 200, 3), 128, np.uint8)))
    deadline = time.monotonic() + 5.0
    while sum(m["type"] == "face_guide" for m in sent) <= done:
        assert time.monotonic() < deadline, "face thread stuck"
        time.sleep(0.005)


def test_a_neighbours_face_at_the_edge_of_the_head_crop_is_not_taken(monkeypatch):
    from trackxen.vision.tracker import TrackOut

    image = np.zeros((1080, 1920, 3), np.uint8)
    gallery = [{"id": "A", "feature": one_hot(0)}, {"id": "B", "feature": one_hot(1)}]
    # A faces away (no face keypoints): the crop is the top of A's box, 180 px square; only B's face
    # is in it, at its edge
    sent, worker, _ = face_worker(monkeypatch, [face_row(120, 40, 60, 70)], {120: one_hot(1)})
    worker.set_gallery(gallery)
    away = TrackOut(1, [400, 200, 600, 800], 0.9, [[0, 0, 0]] * 17, (0, 0), 0, 1.0)
    worker.submit("cam1", image, [away], 1.0)
    settle(worker, sent)
    assert worker.identity("cam1", 1) is None
    # A faces the camera: A's face in the middle of the crop, B's bigger one at its edge
    sent, worker, _ = face_worker(monkeypatch, [face_row(71, 70, 50, 60), face_row(140, 50, 52, 80)],
                                  {71: one_hot(0), 140: one_hot(1)})
    worker.set_gallery(gallery)
    kp = [[500, 260, 0.9], [485, 250, 0.9], [515, 250, 0.9], [460, 255, 0.9], [540, 255, 0.9]] + [[0, 0, 0]] * 12
    facing = TrackOut(1, [400, 200, 600, 800], 0.9, kp, (0, 0), 0, 1.0)
    worker.submit("cam1", image, [facing], 1.0)
    settle(worker, sent)
    assert worker.identity("cam1", 1)["id"] == "A"


def test_closing_the_registration_frame_during_a_capture_keeps_it_closed(monkeypatch):
    import cv2

    from trackxen.vision.worker import VisionLoop

    sent, worker, engine = face_worker(monkeypatch, [face_row(60, 60, 80, 80)], {60: one_hot(0)})
    loop = VisionLoop.__new__(VisionLoop)  # just its face commands
    loop.faces, loop.emit = worker, sent.append
    loop._face_command({"type": "face_guide", "camera": "cam1", "on": True})
    loop._face_command({"type": "face_capture", "camera": "cam1", "capture": 3})
    encode = cv2.imencode

    def closed_meanwhile(*args):  # the operator closes the frame while the face thread is busy
        loop._face_command({"type": "face_guide", "camera": "cam1", "on": False})
        return encode(*args)

    monkeypatch.setattr(cv2, "imencode", closed_meanwhile)
    worker._guide(engine, faces.Job("guide", "cam1", np.full((200, 200, 3), 128, np.uint8), capture=3))
    assert [m["capture"] for m in sent if m["type"] == "face_captured"] == [3]
    assert worker.guides == {}  # no registration frame left switched on


def test_damaged_face_models_are_reported_and_tried_again(tmp_path):
    from trackxen.vision.worker import VisionLoop, WorkerConfig

    for name in (faces.YUNET, faces.SFACE):
        (tmp_path / name).write_bytes(bytes(range(256)) * 4)  # not a model
    sent = []
    loop = VisionLoop.__new__(VisionLoop)  # just its face commands
    loop.cfg, loop.emit, loop.faces = WorkerConfig(sources=[], model_dir=str(tmp_path)), sent.append, None
    loop._face_command({"type": "faces", "gallery": [{"id": "A", "feature": one_hot(0)}]})
    first = loop.faces
    first._thread.join(timeout=5.0)
    errors = [m["message"] for m in sent if m["type"] == "error"]
    assert first.failed and len(errors) == 1 and errors[0].startswith("face recognition unavailable")
    loop._face_command({"type": "face_guide", "camera": "cam1", "on": True})  # tried again
    assert loop.faces is not first and loop.faces.gallery == first.gallery and loop.faces.guides == {"cam1": 0}
    loop.faces._thread.join(timeout=5.0)
    assert sum(m["type"] == "error" for m in sent) == 2


@pytest.mark.skipif(not faces.available(MODELS), reason="face models not downloaded (tools/get_face_models.py)")
def test_same_face_matches_and_another_does_not():
    import cv2

    from trackxen.sim.virtual_camera import sample_photo

    image = sample_photo()
    engine = faces.FaceEngine(MODELS)
    crops = [image[380:500, 70:190], image[390:510, 230:350]]  # two people's heads in the sample photo
    found = [engine.find(c) for c in crops]
    assert all(f is not None for f in found)
    a, b = (engine.fingerprint(c, f) for c, f in zip(crops, found))
    bigger = cv2.resize(crops[0], None, fx=1.6, fy=1.6)
    a2 = engine.fingerprint(bigger, engine.find(bigger))
    assert float(np.dot(a, a2)) > faces.SAME_PERSON > float(np.dot(a, b))
    t0 = time.perf_counter()
    for _ in range(10):
        engine.fingerprint(crops[0], engine.find(crops[0]))
    assert (time.perf_counter() - t0) / 10 < 0.05  # well under 50 ms per look on the CPU


def picked_person(cx=500):
    """A person facing the camera, left of the middle of a 1920-wide picture: their head square is 240 px."""
    from trackxen.vision.tracker import TrackOut

    kp = [[cx, 260, 0.9], [cx - 15, 250, 0.9], [cx + 15, 250, 0.9], [cx - 40, 255, 0.9], [cx + 40, 255, 0.9]]
    return TrackOut(4, [cx - 200, 200, cx + 200, 900], 0.9, kp + [[0, 0, 0]] * 12, (0, 0), 0, 1.0)


def test_a_face_is_registered_from_the_person_clicked_anywhere_in_the_picture(monkeypatch):
    """Not only in the centred frame: the frame follows the chosen person's head, and Capture takes that face."""
    from trackxen.vision.worker import VisionLoop

    sent, worker, _ = face_worker(monkeypatch, [face_row(65, 65, 110, 110)], {65: one_hot(0)})
    loop = VisionLoop.__new__(VisionLoop)
    loop.faces, loop.emit = worker, sent.append
    loop._face_command({"type": "face_guide", "camera": "cam1", "on": True, "track": 4})
    loop._face_command({"type": "face_capture", "camera": "cam1", "capture": 5})
    worker.submit("cam1", np.full((1080, 1920, 3), 128, np.uint8), [picked_person()], 1.0)
    settle(worker, sent)
    guide = next(m for m in sent if m["type"] == "face_guide" and m["camera"] == "cam1")
    assert guide["ok"] and guide["frame"] == [round(380 / 1920, 4), round(135 / 1080, 4), 0.125, round(240 / 1080, 4)]  # (head centre 500, 255)
    captured = [m for m in sent if m["type"] == "face_captured"]
    assert [m["capture"] for m in captured] == [5] and captured[0]["feature"][0] == pytest.approx(1.0)
    # back to the centred frame: no person chosen any more
    loop._face_command({"type": "face_guide", "camera": "cam1", "on": True})
    assert worker.picks == {}


def test_a_chosen_person_who_left_or_whose_face_is_too_small_is_reported(monkeypatch):
    sent, worker, _ = face_worker(monkeypatch, [face_row(75, 75, 90, 90)], {75: one_hot(0)})
    worker.guides["cam1"], worker.picks["cam1"] = 0, 9  # person 9 is not in the picture
    worker.submit("cam1", np.full((1080, 1920, 3), 128, np.uint8), [picked_person()], 1.0)
    assert sent[-1]["problem"].startswith("the chosen person is not in the picture") and not sent[-1]["ok"]
    worker.picks["cam1"] = 4  # in a 4K picture 90 px is under 6 % of its height: too small to register
    worker.submit("cam1", np.full((2160, 3840, 3), 128, np.uint8), [picked_person()], 2.0)
    settle(worker, sent)
    guide = [m for m in sent if m["type"] == "face_guide" and m["camera"] == "cam1"][-1]
    assert not guide["ok"] and guide["problem"] == "too small: zoom in"


def test_the_registration_frame_command_passes_on_the_chosen_person():
    from types import SimpleNamespace

    from trackxen.core.faces import FaceManager

    manager = FaceManager.__new__(FaceManager)
    manager.guides, manager.captures, sent = {}, {}, []
    manager._to_worker = sent.append
    session = SimpleNamespace(cfg=SimpleNamespace(id="cam1", faces=[]))
    assert manager.command(session, "face_guide", {"on": True, "track": 4}) == {"ok": True}
    assert manager.command(session, "face_guide", {"on": True}) == {"ok": True}
    assert [m["track"] for m in sent] == [4, None]
    assert manager.command(session, "face_guide", {"on": True, "track": "4"})["ok"] is False
    assert manager.command(session, "face_guide", {"on": True, "track": True})["ok"] is False

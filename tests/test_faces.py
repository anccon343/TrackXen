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


@pytest.mark.skipif(not faces.available(MODELS), reason="face models not downloaded (tools/get_face_models.py)")
def test_same_face_matches_and_another_does_not():
    import cv2

    from trackxen.sim.virtual_camera import ultralytics_image

    image = ultralytics_image("bus.jpg")
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

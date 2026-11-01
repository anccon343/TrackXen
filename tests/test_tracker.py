import numpy as np

from trackxen.vision.tracker import PersonTracker, greedy_match, iou_matrix
from trackxen.vision.yolo_pose import Person


def person(x: float, y: float = 300, w: float = 100, h: float = 300, score: float = 0.9) -> Person:
    return Person(np.array([x - w / 2, y - h / 2, x + w / 2, y + h / 2]), score, np.zeros((17, 3)))


def test_iou_and_matching():
    a = np.array([[0, 0, 10, 10], [100, 100, 110, 110]], dtype=float)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10]], dtype=float)
    ious = iou_matrix(a, b)
    assert ious[0, 0] == 1.0 and abs(ious[0, 1] - 1 / 3) < 1e-9 and ious[1].max() == 0
    matches, rows, cols = greedy_match(ious, 0.3)
    assert matches == [(0, 0)] and rows == {1} and cols == {1}


def test_ids_stay_with_people_walking_past_each_other():
    tracker = PersonTracker()
    ids_a, ids_b = set(), set()
    for i in range(60):  # 2 s at 30 fps; A walks right, B walks left, they pass at frame 30
        t = i / 30
        a, b = 200 + i * 10, 800 - i * 10
        if abs(a - b) < 60:  # while overlapping only one of them is detected
            people = [person(a)]
        else:
            people = [person(a), person(b, score=0.85)]
        for tr in tracker.update(people, t):
            cx = (tr.box[0] + tr.box[2]) / 2
            if tr.missed == 0 and abs(a - b) > 120:  # only judge while they're clearly apart
                (ids_a if abs(cx - a) < abs(cx - b) else ids_b).add(tr.id)
    assert len(ids_a) == 1, ids_a  # A kept one identity the whole way
    assert len(ids_b) <= 2  # B may get a new id after being hidden, but never A's
    assert not ids_a & ids_b


def test_track_survives_short_gap_and_dies_after_max_missing():
    tracker = PersonTracker(max_missing=0.5)
    for i in range(5):
        out = tracker.update([person(300 + i * 5)], i / 30)
    track_id = out[0].id
    out = tracker.update([], 6 / 30)  # missed one frame: still reported, predicted
    assert out[0].id == track_id and out[0].missed == 1
    out = tracker.update([person(335)], 7 / 30)
    assert out[0].id == track_id and out[0].missed == 0
    assert tracker.update([], 2.0) == []


def test_low_score_detections_keep_tracks_but_do_not_start_them():
    tracker = PersonTracker()
    for i in range(3):
        tracker.update([person(300)], i / 30)
    out = tracker.update([person(300, score=0.3)], 3 / 30)
    assert len(out) == 1 and out[0].missed == 0
    assert tracker.update([person(900, score=0.3)], 4 / 30)[0].missed == 1  # no new track at 900


def test_duplicate_box_of_one_person_never_becomes_a_second_track():
    tracker = PersonTracker()
    for i in range(30):
        sliver = Person(np.array([480.0, 160, 520, 440]), 0.5, np.zeros((17, 3)))  # inside the person, narrow
        tracks = tracker.update([person(500), sliver], i / 30)
    assert len([t for t in tracks if t.missed == 0]) == 1


def test_nms_drops_near_identical_boxes_only():
    from trackxen.vision.boxes import nms

    boxes = np.array([[0, 0, 100, 300], [2, 1, 101, 302], [60, 0, 160, 300]], dtype=float)
    assert nms(boxes, np.array([0.7, 0.9, 0.8]), 0.7) == [1, 2]  # the weaker twin goes, the neighbour stays

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


def face(cx: float, cy: float, span: float, c: float = 0.9) -> np.ndarray:
    """Keypoints with a face seen surely (nose, eyes, ears) at this place and size."""
    k = np.zeros((17, 3))
    k[:5] = [[cx, cy, c], [cx - span * 0.25, cy - span * 0.1, c], [cx + span * 0.25, cy - span * 0.1, c],
             [cx - span * 0.5, cy, c], [cx + span * 0.5, cy, c]]
    return k


def test_one_frame_to_confirm_reports_a_new_person_at_once():
    assert len(PersonTracker(confirm_hits=1).update([person(300)], 0.0)) == 1
    assert PersonTracker(confirm_hits=2).update([person(300)], 0.0) == []


def test_speaker_in_front_of_a_screen_showing_a_person_gets_a_track():
    screen = Person(np.array([560, 40, 1360, 1000.]), 0.85, face(960, 300, 280))  # a close-up on an LED wall
    speaker = Person(np.array([880, 380, 1040, 980.]), 0.7, face(960, 410, 36))  # inside it, a head of their own
    tracker = PersonTracker()
    for i in range(5):
        tracks = tracker.update([screen, speaker], i / 30)
    assert len(tracks) == 2
    tracker = PersonTracker()  # lost for longer than max_missing while the screen stays: found again
    for i in range(5):
        tracker.update([screen, speaker], i / 30)
    for i in range(5, 60):
        tracker.update([screen], i / 30)
    for i in range(60, 65):
        tracks = tracker.update([screen, speaker], i / 30)
    assert len([t for t in tracks if t.missed == 0]) == 2


def test_smaller_box_with_the_same_head_is_still_part_of_the_person():
    tracker = PersonTracker()
    whole = Person(np.array([450, 150, 550, 450.]), 0.9, face(500, 175, 40))
    upper = Person(np.array([480, 160, 520, 300.]), 0.5, face(501, 176, 40))  # the same face, a smaller box
    fewer = face(499, 175, 40)
    fewer[3:5, 2] = 0.2  # same person, only nose and eyes sure in this box
    upper_fewer = Person(np.array([470, 150, 530, 290.]), 0.6, fewer)
    for i in range(30):
        tracks = tracker.update([whole, upper, upper_fewer], i / 30)
    assert len([t for t in tracks if t.missed == 0]) == 1


def test_start_below_sure_detection_starts_tracks_from_weaker_detections():
    tracker = PersonTracker(new_track_score=0.2, high_score=0.3)
    for i in range(3):
        tracks = tracker.update([person(300, score=0.25)], i / 30)
    assert len(tracks) == 1
    tracker = PersonTracker()  # the defaults: 0.25 neither starts a track nor counts as sure
    assert all(tracker.update([person(300, score=0.25)], i / 30) == [] for i in range(3))


def test_keep_above_sure_detection_does_not_drop_sure_detections():
    tracker = PersonTracker(low_score=0.4, high_score=0.3)
    for i in range(3):
        tracks = tracker.update([person(300, score=0.37)], i / 30)
    assert len(tracks) == 1


def test_weak_detections_never_match_more_easily_than_sure_ones():
    def missed_after_step(score: float, overlap: float) -> int:
        tracker = PersonTracker(match_iou=overlap)
        for i in range(3):
            tracker.update([person(300)], i / 30)
        return tracker.update([person(338, score=score)], 3 / 30)[0].missed  # IoU 0.45

    assert missed_after_step(0.9, 0.6) == 1 and missed_after_step(0.2, 0.6) == 1  # both too little overlap
    assert missed_after_step(0.9, 0.2) == 0 and missed_after_step(0.2, 0.2) == 0


def test_new_frame_size_rescales_the_tracks():
    tracker = PersonTracker()
    for i in range(10):  # 1920x1080
        tracks = tracker.update([person(300, 400, 150, 450), person(1000, 600, 150, 450)], i / 30)
    ids = sorted(t.id for t in tracks)
    tracker.rescale(1280 / 1920, 720 / 1080)  # the source switched to 1280x720
    tracks = tracker.update([person(200, 266.7, 100, 300), person(666.7, 400, 100, 300)], 10 / 30)
    assert sorted(t.id for t in tracks if t.missed == 0) == ids
    for t in tracks:
        assert t.box[2] <= 1280 and t.box[3] <= 720
    np.testing.assert_allclose(sorted(t.box for t in tracks)[0], [150, 116.7, 250, 416.7], atol=2)

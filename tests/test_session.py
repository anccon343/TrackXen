import time

from trackxen.control.camera import Pose
from trackxen.core.config import CameraConfig
from trackxen.core.session import ANGLE_FIXED, LOST, NO_TARGET, SEARCHING, TRACKING, CameraSession
from trackxen.core.tracking_settings import PresetLink

W, H = 1920, 1080
ZOOM = 2266.0  # 40-degree field of view with the default lens


async def _done():
    pass


class FakeLink:
    """Stands in for CameraLink: a camera at a settable position; records what was asked of it."""

    def __init__(self):
        self.requests, self.gotos, self.presets = [], [], []
        self.visca_ok, self.freed_age = True, 0.01
        self.commanding, self.last_motion = False, 0.0
        self.samples = [(0.0, 0.0, 0.0, ZOOM)]  # (time, pan, tilt, zoom)

    def set_pose(self, pan, tilt, zoom=ZOOM, t=None):
        self.samples.append((time.monotonic() if t is None else t, pan, tilt, zoom))

    def pose_at(self, t):
        before = [s for s in self.samples if s[0] <= t] or self.samples[:1]
        return Pose(t, *before[-1][1:])

    def pose_now(self):
        return Pose(time.monotonic(), *self.samples[-1][1:])

    def request(self, pan, tilt, zoom=0):
        want = (pan, tilt, zoom)
        if want != (self.requests[-1] if self.requests else (0, 0, 0)):
            self.last_motion = time.monotonic()
        self.requests.append(want)
        self.commanding = want != (0, 0, 0)

    def goto(self, pan, tilt, zoom=None, pan_speed=12, tilt_speed=None):
        self.gotos.append((pan, tilt, zoom))
        return _done()

    def recall_preset(self, number):
        self.presets.append(number)
        return _done()


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, message, level="info", **fields):
        self.events.append((kind, message))

    def said(self, text):
        return any(text in m for _, m in self.events)


def track(tid, cx, cy=540, h=400, age=5.0, missed=0):
    kp = [[cx, cy - h * 0.42, 0.9]] + [[0, 0, 0]] * 16  # nose near the top of the box
    return {"id": tid, "box": [cx - 60, cy - h / 2, cx + 60, cy + h / 2], "score": 0.9, "keypoints": kp,
            "velocity": [0, 0], "missed": missed, "age": age}


def message(tracks):
    now = time.monotonic()
    return {"type": "tracks", "camera": "cam1", "t_frame": now, "t_done": now, "width": W, "height": H,
            "tracks": tracks, "skipped": 0, "timing": {"infer_ms": 5.0}, "batch": 1}


def new_session(**slot):
    log = Log()
    s = CameraSession(CameraConfig(id="cam1", video_delay=0.0), FakeLink(), log=log)
    if slot:
        s.update_slot(slot)
    return s, log


def test_click_selects_the_person_under_it():
    s, _ = new_session()
    s.on_tracks(message([track(1, 400), track(2, 1500)]))
    assert s.select_at(1500 / W, 540 / H) == (2, "") and s.target_id == 2
    assert s.select_at(0.99, 0.02)[0] is None  # nobody near the click


def test_tracking_moves_towards_an_off_centre_target():
    s, _ = new_session()
    s.on_tracks(message([track(7, 1600)]))
    s.select_id(7)
    s.set_tracking(True)
    for _ in range(5):
        s.on_tracks(message([track(7, 1600)]))
        s.tick(time.monotonic())
        time.sleep(0.02)
    assert s.state == TRACKING
    assert s.link.requests[-1][0] > 0  # panning right, towards the person


def test_lost_target_holds_the_camera():
    s, log = new_session()
    s.on_tracks(message([track(3, 960)]))
    s.select_id(3)
    s.set_tracking(True)
    s.last_seen = time.monotonic() - 5.0  # not seen for 5 s
    s.tick(time.monotonic())
    assert s.state == LOST and s.link.requests[-1] == (0, 0, 0)
    assert log.said("target lost") and not s.link.gotos  # "maintain viewing angle"


def test_lost_target_returns_to_the_initial_position_after_the_set_time():
    s, log = new_session(initial={"set": True, "pan": 10.0, "tilt": 2.0, "zoom": 1000.0},
                         lost_action="return", return_after=0.5)
    s.on_tracks(message([track(3, 960)]))
    s.select_id(3)
    s.set_tracking(True)
    assert not s.link.gotos  # a target was already picked: tracking starts where the camera is
    now = time.monotonic()
    s.last_seen = now - 5.0
    s.tick(now)
    assert s.state == LOST and not s.link.gotos
    s.tick(now + 0.6)
    assert s.link.gotos == [(10.0, 2.0, 1000.0)] and log.said("moving to initial position")


def test_tracking_starts_from_the_initial_position():
    s, _ = new_session(initial={"set": True, "pan": -5.0, "tilt": 1.0, "zoom": 500.0})
    s.set_tracking(True)
    assert s.link.gotos == [(-5.0, 1.0, 500.0)]


def test_target_is_re_found_under_a_new_id():
    s, log = new_session()
    s.on_tracks(message([track(3, 960)]))
    s.select_id(3)
    s.set_tracking(True)
    s.on_tracks(message([track(3, 960)]))
    s.on_tracks(message([track(9, 990, age=0.2)]))  # id 3 gone; a new track appears close by
    assert s.target_id == 9 and s.state == TRACKING
    assert log.said("re-found as person 9")


def test_ambiguous_candidates_are_not_adopted():
    s, _ = new_session()
    s.on_tracks(message([track(3, 960)]))
    s.select_id(3)
    s.on_tracks(message([track(3, 960)]))
    s.on_tracks(message([track(8, 900, age=0.2), track(9, 1020, age=0.2)]))  # two equally close
    assert s.target_id == 3


def test_old_tracks_are_not_mistaken_for_the_target():
    s, _ = new_session()
    s.on_tracks(message([track(3, 960), track(4, 1000, age=60.0)]))
    s.select_id(3)
    s.on_tracks(message([track(3, 960), track(4, 1000, age=60.0)]))
    s.on_tracks(message([track(4, 1000, age=60.0)]))  # person 4 was there all along: not the target
    assert s.target_id == 3


def test_releasing_manual_control_stops_the_camera():
    s, _ = new_session()
    s.manual(5, 0, 0, hold=0.0)
    assert s.link.requests[-1] == (5, 0, 0)
    s.tick(time.monotonic() + 0.01)
    assert s.link.requests[-1] == (0, 0, 0)


def test_status_without_auto_select_is_no_target_and_with_it_searching():
    s, _ = new_session()
    s.set_tracking(True)
    assert s.state == NO_TARGET
    s.update_slot({"auto_select": {"enabled": True}})
    assert s.state == SEARCHING


def test_auto_select_picks_a_person_after_the_start_time():
    s, log = new_session(auto_select={"enabled": True, "start_time": 0.3})
    s.set_tracking(True)
    s.on_tracks(message([track(1, 960)]))
    assert s.target_id is None  # not long enough yet
    time.sleep(0.35)
    s.on_tracks(message([track(1, 960)]))
    assert s.target_id == 1 and s.state == TRACKING and log.said("(auto select)")


def test_auto_select_ignores_exclusion_areas_and_people_outside_its_area():
    # person 1 at the centre (pan 0), person 2 at the right (pan ~11.6)
    s, _ = new_session(auto_select={"enabled": True, "start_time": 0.0,
                                    "area": {"enabled": True, "left": -15, "right": 15, "top": 20, "bottom": -20}},
                       exclusion_areas={"0": {"enabled": True, "left": -3, "right": 3, "top": 10, "bottom": -10}})
    s.set_tracking(True)
    s.on_tracks(message([track(1, 960), track(2, 1500)]))
    assert s.target_id == 2  # 1 is excluded
    s.select_id(None)
    s.update_slot({"auto_select": {"area": {"left": -15, "right": 5}}})  # 2 is now outside the area
    s.on_tracks(message([track(1, 960), track(2, 1500)]))
    assert s.target_id is None


def test_tracking_halting_area_releases_the_target_and_blocks_clicks():
    s, log = new_session(halting_areas={"0": {"enabled": True, "left": 8, "right": 20, "top": 10, "bottom": -10}})
    s.on_tracks(message([track(1, 960), track(2, 1500)]))
    assert s.select_at(1500 / W, 540 / H) == (None, "that person is inside a tracking halting area")
    s.select_id(1)
    s.set_tracking(True)
    s.on_tracks(message([track(1, 1500)]))  # the target walks into the halting area
    assert s.target_id is None and s.lost_since is not None
    assert log.said("entered tracking halting area 1")


def test_fixed_viewing_angle_area_holds_the_shot_while_the_target_is_inside():
    s, log = new_session(fixed_areas={"0": {"enabled": True, "left": -5, "right": 5, "top": 10, "bottom": -5,
                                            "view": {"set": True, "pan": 0.5, "tilt": 3.0, "zoom": 4000.0}}})
    s.on_tracks(message([track(1, 960)]))
    s.select_id(1)
    s.set_tracking(True)
    s.on_tracks(message([track(1, 960)]))
    assert s.state == ANGLE_FIXED and s.link.gotos == [(0.5, 3.0, 4000.0)]
    s.tick(time.monotonic())
    assert s.link.requests[-1] == (0, 0, 0)  # no tracking moves while the angle is fixed
    s.on_tracks(message([track(1, 1000)]))  # still inside: stays fixed
    assert s.state == ANGLE_FIXED
    s.on_tracks(message([track(1, 1700)]))  # walks out (pan ~15.7)
    assert s.state == TRACKING and log.said("left fixed viewing angle area 1")


def test_priority_display_area_disables_fixed_areas():
    s, _ = new_session(priority_area={"enabled": True, "left": -20, "right": -10, "top": 5, "bottom": -5},
                       fixed_areas={"0": {"enabled": True, "left": -5, "right": 5, "top": 10, "bottom": -5}})
    s.on_tracks(message([track(1, 960)]))
    s.select_id(1)
    s.set_tracking(True)
    s.on_tracks(message([track(1, 960)]))
    assert s.state == TRACKING and s.fixed is None
    assert any("can't be used together" in p for p in s.status()["problems"])


def test_camera_moved_by_another_controller_stops_tracking():
    s, log = new_session()
    s.set_tracking(True)
    now = time.monotonic()
    s.link.set_pose(0.0, 0.0, t=now - 1.0)
    s.link.set_pose(2.0, 0.0, t=now - 0.05)  # moved 2 degrees, and we sent no movement
    s.tick(now)
    assert not s.tracking and log.said("moved by another controller")


def test_tracking_restarts_after_manual_operation_when_set_to():
    s, log = new_session(restart_after_manual=True)
    s.set_tracking(True)
    now = time.monotonic()
    s.link.set_pose(0.0, 0.0, t=now - 1.0)
    s.link.set_pose(2.0, 0.0, t=now - 0.05)
    s.tick(now)
    assert s.tracking and s.paused
    s.link.set_pose(2.0, 0.0, t=now + 0.1)  # the camera stops
    s.tick(now + 0.5)
    s.tick(now + 1.6)
    assert not s.paused and log.said("tracking again")


def test_joystick_turns_tracking_off_unless_restart_is_on():
    s, log = new_session()
    s.set_tracking(True)
    s.manual(5, 0, 0)
    assert not s.tracking and log.said("Restart Tracking After Manual Operation is off")
    s2, _ = new_session(restart_after_manual=True)
    s2.set_tracking(True)
    s2.manual(5, 0, 0)
    assert s2.tracking


def test_preset_link_on_starts_tracking_with_its_app_settings_after_the_move():
    s, log = new_session()
    s.cfg.preset_links[3] = PresetLink(auto_tracking="on", slot=2)
    s.set_tracking(True)
    s.recall_preset(3)
    assert s.link.presets == [2] and not s.tracking and s.cfg.active_slot == 2  # VISCA counts from 0
    s.move.started -= 1.0  # the camera has been still for a while: it is there
    s.tick(time.monotonic())
    assert s.tracking and s.move is None and log.said("Camera Preset Link: On")


def test_preset_link_off_stops_tracking():
    s, _ = new_session()
    s.cfg.preset_links[1] = PresetLink(auto_tracking="off")
    s.set_tracking(True)
    s.recall_preset(1)
    assert not s.tracking


def test_areas_are_drawn_in_the_current_picture():
    s, _ = new_session(exclusion_areas={"1": {"enabled": True, "left": -10, "right": 10, "top": 5, "bottom": -5}})
    areas = s.status()["areas"]
    assert [(a["kind"], a["index"]) for a in areas] == [("exclusion", 1)]
    x1, y1, x2, y2 = areas[0]["rect"]
    assert abs((x1 + x2) / 2 - 0.5) < 1e-3 and abs((y1 + y2) / 2 - 0.5) < 1e-3 and 0.2 < x1 < 0.3


def test_on_air_blends_to_the_calm_motion_profile():
    s, _ = new_session()
    normal_pan, normal_dead = s.controller.motion.max_pan, s.controller.framing.dead_zone_x
    s.set_on_air(True)
    for _ in range(25):  # half a second: half way
        s.tick(time.monotonic())
    assert 0.4 < s.air < 0.6
    for _ in range(30):
        s.tick(time.monotonic())
    assert s.air == 1.0
    assert s.controller.motion.max_pan == normal_pan * 0.5 and s.controller.motion.max_zoom_step == 1
    assert s.controller.framing.dead_zone_x == normal_dead * 1.5
    s.set_on_air(False)
    for _ in range(60):
        s.tick(time.monotonic())
    assert s.air == 0.0 and s.controller.motion.max_pan == normal_pan


def test_no_new_target_while_live_until_off_program():
    s, _ = new_session(auto_select={"enabled": True, "start_time": 0.0})
    s.set_tracking(True)
    s.on_air_tally = True
    s.on_tracks(message([track(1, 960)]))
    assert s.target_id is None  # "wait": no visible jump to a new person while on Program
    s.on_air_tally = False
    s.on_tracks(message([track(1, 960)]))
    assert s.target_id == 1


def test_audio_cue_goes_to_the_preset_then_frames_the_person_there():
    s, log = new_session()
    s.audio_cue(1, "Pastor", 3, "", slow=False)
    assert s.link.presets == [2] and s.move is not None and not s.tracking
    s.move.started -= 1.0  # the camera has arrived and stopped
    s.tick(time.monotonic())
    assert s.tracking
    s.on_tracks(message([track(5, 300), track(6, 1000)]))  # 6 is nearest the middle of the picture
    assert s.target_id == 6 and log.said("audio: Pastor") and s.cue is None

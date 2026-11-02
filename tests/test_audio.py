import asyncio
import time

from trackxen.control.camera import Pose
from trackxen.integrations import vmix, xair
from trackxen.rules.audio import AudioFollow, AudioFollowConfig, CameraState, MicMapping, SpeechDetector
from trackxen.sim.fake_mixer import FakeMixer
from trackxen.sim.fake_vmix import FakeVmix


def cfg(**values) -> AudioFollowConfig:
    return AudioFollowConfig.model_validate({"enabled": True, **values})


def levels(ch1: float, ch2: float = -90.0) -> list[float]:
    out = [-90.0] * 40
    out[0], out[1] = ch1, ch2
    return out


def test_speech_needs_the_attack_time_and_survives_short_pauses():
    c, sd = cfg(threshold_db=-40, attack=0.3, hold=2.0), SpeechDetector()
    assert sd.update(levels(-20), 0.0, c) == []
    assert sd.update(levels(-20), 0.2, c) == []  # a cough is not speech
    assert sd.update(levels(-20), 0.35, c) == [1]
    assert sd.update(levels(-70), 1.0, c) == [] and sd.speaking() == [1]  # pause between words
    sd.update(levels(-70), 3.1, c)
    assert sd.speaking() == []  # quiet for the hold time: finished


def test_muted_mic_or_fader_down_never_speaks():
    c, sd = cfg(attack=0.0), SpeechDetector()
    sd.channels[0].muted = True
    sd.channels[1].fader = 0.0
    assert sd.update(levels(-10, -10), 0.0, c) == [] and sd.update(levels(-10, -10), 1.0, c) == []


def mapping(**values) -> MicMapping:
    return MicMapping.model_validate({"channel": 1, "label": "Pastor", "presets": {"cam1": 3, "cam2": 4}, **values})


def test_an_off_air_camera_answers_first():
    c = cfg(mappings=[mapping()])
    cams = [CameraState("cam1", on_air=True), CameraState("cam2")]
    cue = AudioFollow().choose(1, c, cams, 100.0)
    assert cue.camera == "cam2" and not cue.slow


def test_only_the_live_camera_waits_or_moves_slowly():
    cams = [CameraState("cam1", on_air=True)]
    follow = AudioFollow()
    assert follow.choose(1, cfg(mappings=[mapping(presets={"cam1": 3})]), cams, 100.0) is None
    assert 1 in follow.waiting  # tried again when the camera goes off Program
    cue = AudioFollow().choose(1, cfg(live_camera="slow", mappings=[mapping(presets={"cam1": 3})]), cams, 100.0)
    assert cue.camera == "cam1" and cue.slow


def test_the_operator_always_wins():
    c = cfg(manual_grace=10.0, mappings=[mapping()])
    cams = [CameraState("cam1", manual_at=95.0), CameraState("cam2", on_air=True)]
    assert AudioFollow().choose(1, c, cams, 100.0) is None  # cam1 was just used by hand; cam2 is live


def test_no_move_when_a_camera_already_has_that_person():
    c = cfg(mappings=[mapping(face="pastor")])
    cams = [CameraState("cam1", tracking=True, target_face="pastor"), CameraState("cam2")]
    assert AudioFollow().choose(1, c, cams, 100.0) is None


def test_a_camera_framing_a_mics_person_stays_busy_after_the_cue_is_answered():
    """K59: once a camera found the person at mic 1 (no registered face), mic 1 speaking again leaves it
    alone, and mic 2 goes to a camera that isn't framing anybody's mic."""
    c = cfg(mappings=[mapping(), mapping(channel=2, label="Reader", presets={"cam1": 5, "cam2": 6})])
    cams = [CameraState("cam1", tracking=True, framing_mic=1), CameraState("cam2")]
    follow = AudioFollow()
    follow.waiting[1] = 90.0
    assert follow.choose(1, c, cams, 100.0) is None and 1 not in follow.waiting
    assert follow.choose(2, c, cams, 100.0).camera == "cam2"
    cams[0].tracking = False  # (no longer following anybody)
    assert follow.choose(1, c, cams, 100.0).camera == "cam1"


def test_a_session_remembers_whose_mic_it_answered_until_the_target_changes():
    """K59: the session keeps the answered mic while it follows that person; audio-follow sees it."""
    from trackxen.core.config import AppConfig, CameraConfig
    from trackxen.core.live_inputs import LiveInputs
    from trackxen.core.session import CameraSession

    class Link:
        visca_ok, freed_age, commanding, last_motion = True, 0.01, False, 0.0

        def pose_at(self, t):
            return Pose(t, 0.0, 0.0, 2266.0)

        def pose_now(self):
            return Pose(time.monotonic(), 0.0, 0.0, 2266.0)

        def request(self, pan, tilt, zoom=0):
            pass

    def tracks(*ids):
        now = time.monotonic()
        return {"t_frame": now, "t_done": now, "width": 1920, "height": 1080, "timing": {}, "tracks": [
            {"id": i, "box": [900 + 200 * n, 300, 1020 + 200 * n, 900], "score": 0.9,
             "keypoints": [[960 + 200 * n, 330, 0.9]] + [[0, 0, 0]] * 16, "velocity": [0, 0], "missed": 0, "age": 5.0}
            for n, i in enumerate(ids)]}

    s = CameraSession(CameraConfig(id="cam1", video_delay=0.0), Link())
    s.audio_cue(1, "Pastor", None, "", slow=False)  # no preset: take the person in view
    assert s.tracking and s.cue
    s.on_tracks(tracks(4))
    assert s.cue is None and s.target_id == 4 and s.framing_mic == 1

    class App:
        config, sessions = AppConfig(), {"cam1": s}

    states = LiveInputs(App())._camera_states()
    assert states[0].framing_mic == 1 and states[0].busy_with is None
    s.lost_since = time.monotonic()  # lost: the mic speaking again may send it to its place again
    assert LiveInputs(App())._camera_states()[0].framing_mic is None
    s.lost_since = None
    s.on_tracks(tracks(9, 4))  # the target is still followed (and the tracker renumbering keeps it)
    assert s.framing_mic == 1
    s.select_at(0.5, 0.5)  # the operator picks someone else
    assert s.target_id == 9 and s.framing_mic is None


def test_vmix_link_follows_tally_and_matches_ndi_names():
    async def run():
        fake = FakeVmix(cycle_seconds=0.3)
        port = await fake.start(port=0)
        changes = []
        link = vmix.VmixLink("127.0.0.1", port, on_change=lambda: changes.append(list(link.tally)))
        link.start()
        try:
            for _ in range(40):
                await asyncio.sleep(0.05)
                if len(changes) >= 3:
                    break
            assert link.connected and len(link.inputs) == 5
            assert link.suggest("BIRDDOG-CAM2 (CAM)").number == 2
            assert link.state_of("key-1") in (vmix.Tally.PROGRAM, vmix.Tally.PREVIEW, vmix.Tally.OFF)
            assert len({tuple(c) for c in changes}) >= 2  # the tally really changed
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())


def test_mixer_link_reads_levels_and_channel_names():
    async def run():
        fake = FakeMixer()
        port = await fake.start(port=0)
        link = xair.MixerLink("127.0.0.1", port)
        link.start()
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 3 and not (link.connected and link.names[0]):
                await asyncio.sleep(0.05)
            assert link.connected and link.names[0] == "Mic 1" and not link.muted[0]
            assert link.levels[1] == -65.0
        finally:
            link.stop()
            fake.stop()

    asyncio.run(run())

"""vMix tally and audio-follow together (core/live_inputs.py), with links that are never started."""

import time
from types import SimpleNamespace

from trackxen.core.config import AppConfig
from trackxen.core.live_inputs import LiveInputs
from trackxen.integrations.vmix import Tally, VmixInput, VmixLink
from trackxen.integrations.xair import METERS1_COUNT, MixerLink
from trackxen.rules.audio import AudioFollowConfig


class Log:
    def __init__(self):
        self.events = []

    def event(self, kind, message, level="info", **fields):
        self.events.append((kind, message))


class FakeSession:
    """What LiveInputs reads of a camera session; records the audio cues it gets."""

    def __init__(self, cam_id: str, vmix_input: str):
        self.cfg = SimpleNamespace(id=cam_id, name=cam_id, vmix_input=vmix_input)
        self.on_air_tally, self.on_air_manual = False, None
        self.audio_follow, self.tracking, self.target_face, self.manual_at, self.cue = True, False, None, -1e9, None
        self.cues = []

    @property
    def on_air(self) -> bool:
        return self.on_air_tally if self.on_air_manual is None else self.on_air_manual

    def audio_cue(self, channel, label, preset, face, slow):
        self.cues.append((channel, label, preset, face, slow))


def setup(**audio):
    """cam1 (vMix input 1) is live on Program; mic 1 (the pastor) may only be answered by cam1."""
    config = AppConfig()
    config.audio = AudioFollowConfig.model_validate({
        "enabled": True, "attack": 0.0, "hold": 2.0, "live_camera": "wait",
        "mappings": [{"channel": 1, "label": "Pastor", "presets": {"cam1": 3}}], **audio})
    cam1 = FakeSession("cam1", "1")
    app = SimpleNamespace(config=config, sessions={"cam1": cam1}, log=Log())
    inputs = LiveInputs(app)
    inputs.vmix = VmixLink("vmix.test", on_change=inputs.on_tally)
    inputs.vmix.inputs = [VmixInput(1, "k1", "Camera 1", "NDI", "Cam 1")]
    inputs.vmix.connected, inputs.vmix.tally = True, [Tally.PROGRAM]
    inputs.mixer = MixerLink("mixer.test", on_levels=inputs.on_levels)
    inputs.on_tally()
    assert cam1.on_air_tally
    return inputs, cam1


def speak(inputs: LiveInputs, db: float = -10.0) -> None:
    """One meter reading from the mixer with mic 1 at `db`."""
    levels = [-90.0] * METERS1_COUNT
    levels[0] = db
    inputs.mixer._meters(levels)


def test_a_mic_waits_for_the_live_camera_and_is_answered_when_it_goes_off_program():
    inputs, cam1 = setup()
    speak(inputs)
    assert 1 in inputs.follow.waiting and cam1.cues == []  # only the live camera could answer: wait
    inputs.vmix.tally = [Tally.PREVIEW]  # the director cut away
    inputs.on_tally()
    assert not cam1.on_air_tally and cam1.cues == [(1, "Pastor", 3, "", False)]


def test_losing_vmix_keeps_the_live_camera_on_air():
    inputs, cam1 = setup()
    speak(inputs)
    assert 1 in inputs.follow.waiting
    inputs.vmix.connected, inputs.vmix.tally = False, []  # what VmixLink does when the connection drops
    inputs.on_tally()
    speak(inputs)
    assert cam1.on_air_tally and cam1.cues == []  # still live as far as anyone knows: no preset recall
    inputs.vmix.connected = True  # back, and the camera is really off Program now
    inputs.vmix.tally = [Tally.OFF]
    inputs.on_tally()
    assert not cam1.on_air_tally and len(cam1.cues) == 1


def test_a_new_vmix_link_without_tally_yet_changes_nothing():
    inputs, cam1 = setup()
    speak(inputs)
    inputs.vmix = VmixLink("vmix.test", on_change=inputs.on_tally)  # "Apply connections" restarts the link
    inputs.on_tally()
    speak(inputs)
    assert cam1.on_air_tally and cam1.cues == []


def test_no_vmix_set_up_means_off_program():
    inputs, cam1 = setup()
    inputs.vmix = None
    inputs.on_tally()
    assert not cam1.on_air_tally


def test_switching_audio_follow_off_drops_the_waiting_mic():
    inputs, cam1 = setup()
    speak(inputs)
    assert 1 in inputs.follow.waiting
    inputs.app.config.audio = inputs.app.config.audio.model_copy(update={"enabled": False})
    inputs.vmix.tally = [Tally.OFF]
    inputs.on_tally()
    assert cam1.cues == [] and inputs.follow.waiting == {}


def test_a_silent_mixer_drops_the_waiting_mic():
    inputs, cam1 = setup()
    speak(inputs)
    assert 1 in inputs.follow.waiting
    inputs.mixer.last_levels = time.monotonic() - 5.0  # no levels for 5 s: the speech state is stale
    inputs.vmix.tally = [Tally.OFF]
    inputs.on_tally()
    assert cam1.cues == [] and inputs.follow.waiting == {}

"""What the room tells TrackXen besides the video: vMix tally (which camera is live on Program) and
the mixer's microphones (audio-follow), for all cameras together."""

from __future__ import annotations

import time

from trackxen.integrations.vmix import Tally, VmixLink
from trackxen.integrations.xair import INPUT_CHANNELS, MixerLink
from trackxen.rules.audio import AudioFollow, CameraState, SpeechDetector


class LiveInputs:
    def __init__(self, app):
        self.app = app
        self.vmix: VmixLink | None = None
        self.mixer: MixerLink | None = None
        self.speech = SpeechDetector(INPUT_CHANNELS)
        self.follow = AudioFollow()

    def start(self) -> None:
        """(Re)connect vMix and the mixer, when their addresses are set."""
        self.stop()
        cfg = self.app.config
        if cfg.vmix.host:
            self.vmix = VmixLink(cfg.vmix.host, cfg.vmix.port, self.on_tally, self.app.log)
            self.vmix.start()
        if cfg.mixer.host:
            self.mixer = MixerLink(cfg.mixer.host, cfg.mixer.port, self.on_levels, self.app.log)
            self.mixer.start()

    def stop(self) -> None:
        for link in (self.vmix, self.mixer):
            if link:
                link.stop()
        self.vmix = self.mixer = None

    # --- vMix tally ----------------------------------------------------------------------------

    def on_tally(self) -> None:
        """vMix tally changed (or vMix went away): which cameras are on Program now."""
        for s in self.app.sessions.values():
            state = self.vmix.state_of(s.cfg.vmix_input) if self.vmix else None
            live = state == Tally.PROGRAM
            if live != s.on_air_tally:
                s.on_air_tally = live
                self.app.log.event("on air", "on Program (vMix)" if live else "off Program (vMix)", source=s.cfg.id)
        self._answer_waiting(time.monotonic())

    # --- audio-follow ----------------------------------------------------------------------------

    def _camera_states(self) -> list[CameraState]:
        return [CameraState(s.cfg.id, s.on_air, s.audio_follow, s.tracking, s.target_face, s.manual_at,
                            s.cue[3] if s.cue else None) for s in self.app.sessions.values()]

    def on_levels(self, levels: list[float]) -> None:
        """Mixer levels (about 20 a second): who started speaking, and which camera answers."""
        cfg = self.app.config.audio
        if not cfg.enabled or not self.mixer:
            return
        for i, ch in enumerate(self.speech.channels):
            ch.muted, ch.fader, ch.name = self.mixer.muted[i], self.mixer.faders[i], self.mixer.names[i]
        now = time.monotonic()
        started = self.speech.update(levels, now, cfg)
        order = [m.channel for m in cfg.mappings]
        for channel in sorted(started, key=lambda c: order.index(c) if c in order else len(order)):
            self._cue(channel, now)
        self._answer_waiting(now)

    def _answer_waiting(self, now: float) -> None:
        """Mics that are still speaking but found only live cameras: maybe one is free now."""
        speaking = set(self.speech.speaking())
        for channel in list(self.follow.waiting):
            if channel not in speaking:
                self.follow.waiting.pop(channel)
            else:
                self._cue(channel, now)

    def _cue(self, channel: int, now: float) -> None:
        cue = self.follow.choose(channel, self.app.config.audio, self._camera_states(), now)
        if cue is None:
            return
        session = self.app.sessions[cue.camera]
        label = cue.mapping.label or self.speech.channels[channel - 1].name or f"mic {channel}"
        self.app.log.event("audio", f"{label} speaking: {session.cfg.name} answers ({cue.reason})", source=cue.camera)
        session.audio_cue(channel, label, cue.mapping.presets.get(cue.camera), cue.mapping.face, cue.slow)

    # --- status ----------------------------------------------------------------------------------

    def status(self) -> dict:
        vmix, mixer = self.vmix, self.mixer
        return {
            "vmix": None if not vmix else {
                "host": vmix.host, "connected": vmix.connected, "error": vmix.error,
                "inputs": [{"number": i.number, "key": i.key, "title": i.title, "type": i.type,
                            "tally": int(vmix.tally[i.number - 1]) if i.number <= len(vmix.tally) else 0}
                           for i in vmix.inputs],
                "suggested": {s.cfg.id: (getattr(vmix.suggest(s.cfg.ndi), "key", "") if s.cfg.ndi else "")
                              for s in self.app.sessions.values()}},
            "mixer": None if not mixer else {
                "host": mixer.host, "connected": mixer.connected, "error": mixer.error,
                "channels": [{"channel": i + 1, "name": mixer.names[i], "level": round(mixer.levels[i], 1),
                              "muted": mixer.muted[i], "speaking": self.speech.channels[i].speaking}
                             for i in range(INPUT_CHANNELS)]},
        }

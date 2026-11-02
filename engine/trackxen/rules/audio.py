"""Audio-follow: from mixer levels to "camera X: go and frame the person at mic Y".

1. **Speech detection** per mic channel: speaking once the level stays above the threshold for
   the attack time; still speaking until it stays below for the hold time (pauses between
   words don't count). Muted channels and faders all the way down never speak.
2. **Choosing** (when a mic starts speaking, highest priority first): among the cameras that
   may answer that mic, prefer one that is not on Program, so the director can cut to it; skip
   cameras the operator touched recently (manual control always wins) and cameras already on
   that person. If only the live camera could answer, it waits, or moves slowly (a setting).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


class MicMapping(BaseModel):
    """One mixer channel and who is at it."""

    channel: int = Field(ge=1, le=16)
    label: str = Field("", max_length=40)  # shown in the UI; the mixer's channel name by default
    face: str = ""  # registered face id of the person who uses this mic (optional)
    presets: dict[str, int] = Field(default_factory=dict)  # camera id -> preset showing this person's place
    cameras: list[str] = Field(default_factory=list)  # cameras that may answer; empty = any with a preset or face


class AudioFollowConfig(BaseModel):
    enabled: bool = False
    threshold_db: float = Field(-40.0, ge=-90.0, le=0.0)
    attack: float = Field(0.3, ge=0.0, le=3.0)  # seconds above the threshold before it counts as speech
    hold: float = Field(2.0, ge=0.0, le=15.0)  # seconds of quiet before the speaker counts as finished
    live_camera: Literal["wait", "slow"] = "wait"  # if only the camera on Program could answer
    manual_grace: float = Field(10.0, ge=0.0, le=120.0)  # seconds a camera is left alone after the operator used it
    mappings: list[MicMapping] = Field(default_factory=list)  # in priority order, highest first


@dataclass
class Channel:
    level: float = -90.0
    muted: bool = False
    fader: float = 0.75  # 0..1 like the mixer (0.75 = 0 dB)
    name: str = ""
    above_since: float | None = None
    below_since: float | None = None
    speaking: bool = False
    since: float = 0.0  # when speaking started


class SpeechDetector:
    """Per channel: attack before speaking counts, hold before it ends."""

    def __init__(self, channels: int = 16):
        self.channels = [Channel() for _ in range(channels)]

    def update(self, levels_db: list[float], now: float, cfg: AudioFollowConfig) -> list[int]:
        """Feed one meter reading; returns the channels (1-based) that just started speaking."""
        started = []
        for i, ch in enumerate(self.channels):
            if i >= len(levels_db):
                break
            ch.level = levels_db[i]
            loud = ch.level >= cfg.threshold_db and not ch.muted and ch.fader > 0.05
            if loud:
                ch.below_since = None
                ch.above_since = ch.above_since if ch.above_since is not None else now
                if not ch.speaking and now - ch.above_since >= cfg.attack:
                    ch.speaking, ch.since = True, now
                    started.append(i + 1)
            else:
                ch.above_since = None
                ch.below_since = ch.below_since if ch.below_since is not None else now
                if ch.speaking and now - ch.below_since >= cfg.hold:
                    ch.speaking = False
        return started

    def speaking(self) -> list[int]:
        return [i + 1 for i, ch in enumerate(self.channels) if ch.speaking]

    def reset(self) -> None:
        """Forget who is speaking (no levels for a while): a channel speaks again only after its attack."""
        for ch in self.channels:
            ch.speaking, ch.above_since, ch.below_since = False, None, None


@dataclass
class CameraState:
    """What audio-follow needs to know about a camera."""

    id: str
    on_air: bool = False
    follow: bool = True  # audio-follow allowed for this camera
    tracking: bool = False
    target_face: str | None = None  # registered face of its current target
    manual_at: float = -1e9  # last time the operator moved or picked on it
    busy_with: int | None = None  # the mic channel it is answering
    framing_mic: int | None = None  # the mic whose person it found and still follows (an answered cue)


@dataclass
class Cue:
    camera: str
    mapping: MicMapping
    slow: bool = False  # the camera is live: move with the on-air profile
    reason: str = ""


@dataclass
class AudioFollow:
    """Decides which camera answers a mic that started speaking."""

    waiting: dict[int, float] = field(default_factory=dict)  # channel -> since (only live cameras could answer)

    def choose(self, channel: int, cfg: AudioFollowConfig, cameras: list[CameraState], now: float) -> Cue | None:
        mapping = next((m for m in cfg.mappings if m.channel == channel), None)
        if mapping is None:
            return None
        able = [c for c in cameras if c.follow and self._may_answer(mapping, c.id)]
        if any(c.tracking and (c.framing_mic == channel or (c.target_face and mapping.face
                                                            and c.target_face == mapping.face)) for c in able):
            self.waiting.pop(channel, None)
            return None  # a camera is already on that person
        free = [c for c in able if now - c.manual_at >= cfg.manual_grace]
        off_air = [c for c in free if not c.on_air]
        if off_air:  # prefer one not answering or framing another mic, then the order of the camera list
            pick = min(off_air, key=lambda c: (c.busy_with is not None or (c.tracking and c.framing_mic is not None),
                                               cameras.index(c)))
            self.waiting.pop(channel, None)
            return Cue(pick.id, mapping, reason="off air")
        if free and cfg.live_camera == "slow":
            self.waiting.pop(channel, None)
            return Cue(free[0].id, mapping, slow=True, reason="only the live camera can answer: moving slowly")
        if free:
            self.waiting.setdefault(channel, now)  # wait until that camera is off Program
        return None

    @staticmethod
    def _may_answer(mapping: MicMapping, camera: str) -> bool:
        if mapping.cameras:
            return camera in mapping.cameras
        return camera in mapping.presets or bool(mapping.face)

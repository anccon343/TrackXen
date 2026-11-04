"""Canon RA-AT001-style tracking settings ("App Settings"): 5 named slots per camera.

Every area is a rectangle in room angles (pan/tilt degrees), so it stays fixed to the room
while the camera moves. Ranges follow Canon's (docs/reference.md section 3).
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from trackxen.control.controller import Framing, Motion
from trackxen.control.lens import Lens

SLOTS = 5
AREAS = 4  # fixed viewing angle, exclusion and halting areas each
# Composition limits (the pages offer narrower ones): outside them the camera would swing away from the
# person, or the size control would fail (target_height 0, zoom_dead_band -1)
COMPOSITION = {"anchor_x": (0.0, 1.0), "anchor_y": (0.0, 1.0), "dead_zone_x": (0.0, 0.5), "dead_zone_y": (0.0, 0.5),
               "target_height": (0.05, 1.0), "zoom_dead_band": (0.0, 1.0)}


def bad_value(message: str, **values) -> PydanticCustomError:
    """A ValueError for validators whose details can be sent back as JSON (a 422, not a 500)."""
    return PydanticCustomError("value_error", message, values or None)


class FiniteModel(BaseModel):
    """Settings: numbers must be finite (NaN or infinity reaching the controller drives at full speed)."""

    model_config = ConfigDict(allow_inf_nan=False)


class View(FiniteModel):
    """A camera viewing angle: pan/tilt degrees and the raw zoom value."""

    set: bool = False
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0


class Area(FiniteModel):
    """A rectangle in room angles: pan left..right, tilt top..bottom (degrees).

    `view` is the camera angle the area was drawn at: "View" goes back to it, and for a fixed
    viewing angle area it is the shot that is held while the target is inside.
    """

    enabled: bool = False
    left: float = -10.0
    right: float = 10.0
    top: float = 10.0
    bottom: float = -10.0
    view: View = Field(default_factory=View)

    def contains(self, pan: float, tilt: float, margin: float = 0.0) -> bool:
        """Inside, optionally with the area grown by `margin` of its size on every side."""
        mx, my = margin * (self.right - self.left), margin * (self.top - self.bottom)
        return (self.enabled and self.left - mx <= pan <= self.right + mx
                and self.bottom - my <= tilt <= self.top + my)

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2


class ShootingRange(FiniteModel):
    """Step 1: how far the camera may pan/tilt while tracking (the view edges stay inside)."""

    enabled: bool = False
    left: float = -170.0
    right: float = 170.0
    top: float = 90.0
    bottom: float = -90.0


class AutoSelect(FiniteModel):
    """Step 4: a person entering the area (or the whole view) becomes the target."""

    enabled: bool = False
    start_time: float = Field(3.0, ge=0.0, le=10.0)  # seconds in the area before tracking starts
    area: Area = Field(default_factory=Area)


class MultiPerson(FiniteModel):
    enabled: bool = False
    zoom_speed: int = Field(2, ge=1, le=3)
    min_size: int = Field(1, ge=0, le=3)  # higher: stop zooming out sooner
    transition_speed: int = Field(3, ge=1, le=3)  # lower: slower change between single and group


class FaceDirection(FiniteModel):
    enabled: bool = False
    sensitivity: int = Field(2, ge=1, le=3)
    space: int = Field(2, ge=1, le=3)  # how much room to leave in the facing direction
    forward: Literal["maintain", "center"] = "maintain"


class SitStand(FiniteModel):
    enabled: bool = False
    sit_size: float = Field(0.7, ge=0.2, le=0.95)  # person height in the frame when sitting
    stand_size: float = Field(0.5, ge=0.2, le=0.95)  # ... when standing


class OnAir(FiniteModel):
    """Motion while the camera is on Program (vMix tally, or the manual on-air switch): calmer.
    Blended in and out over about a second, so the switch itself doesn't show."""

    enabled: bool = True
    speed: float = Field(0.5, ge=0.1, le=1.0)  # share of the normal top speed
    accel: float = Field(0.35, ge=0.1, le=1.0)  # share of the normal acceleration: softer starts and stops
    dead_zone: float = Field(1.5, ge=1.0, le=3.0)  # the Pan/Tilt Halting Area grows by this
    zoom: Literal["slow", "off"] = "slow"
    retarget: Literal["wait", "slow"] = "wait"  # a new target while live: wait until off Program, or go slowly


class AutoFraming(FiniteModel):
    """Step 8."""

    enabled: bool = False
    multi_person: MultiPerson = Field(default_factory=MultiPerson)
    face_direction: FaceDirection = Field(default_factory=FaceDirection)
    sit_stand: SitStand = Field(default_factory=SitStand)


def _areas() -> list[Area]:
    return [Area() for _ in range(AREAS)]


class AppSettings(FiniteModel):
    """One of the 5 App Settings slots."""

    name: str = Field("", max_length=40)
    # Step 1: pan/tilt operation control
    fix_tilt: bool = False
    shooting_range: ShootingRange = Field(default_factory=ShootingRange)
    # Step 2: the view when tracking starts, and what happens when the target is lost
    initial: View = Field(default_factory=View)
    lost_action: Literal["maintain", "return"] = "maintain"
    return_after: float = Field(3.0, ge=0.0, le=30.0)
    # Step 3: priority display area or fixed viewing angle areas (not both)
    priority_area: Area = Field(default_factory=Area)
    fixed_areas: list[Area] = Field(default_factory=_areas)
    # Step 4: tracking target auto select
    auto_select: AutoSelect = Field(default_factory=AutoSelect)
    restart_after_manual: bool = False
    # Step 5: composition
    composition: Framing = Field(default_factory=Framing)
    halting_area: bool = True  # Pan/Tilt Halting Area on (its size is composition.dead_zone_x/y)
    sensitivity: int = Field(7, ge=1, le=10)  # Canon "Tracking Sensitivity": higher is nimbler
    # Beyond Canon: higher is softer starts, stops and speed changes (tracking and joystick); 1 is the liveliest
    smoothness: int = Field(5, ge=1, le=10)
    # Steps 6 and 7
    exclusion_areas: list[Area] = Field(default_factory=_areas)
    halting_areas: list[Area] = Field(default_factory=_areas)  # tracking halting areas
    # Steps 8 and 9
    auto_framing: AutoFraming = Field(default_factory=AutoFraming)
    registered_faces: bool = False
    # Beyond Canon: the calmer motion while live on Program
    on_air: OnAir = Field(default_factory=OnAir)

    @field_validator("fixed_areas", "exclusion_areas", "halting_areas")
    @classmethod
    def _four(cls, v: list[Area]) -> list[Area]:
        return (v + _areas())[:AREAS]

    @field_validator("composition")
    @classmethod
    def _composition_in_range(cls, v: Framing) -> Framing:
        for name, (lo, hi) in COMPOSITION.items():
            if not lo <= getattr(v, name) <= hi:
                raise bad_value("composition {name} must be {lo}..{hi}", name=name, lo=lo, hi=hi)
        return v

    @model_validator(mode="after")
    def _sit_stand_sets_display_size(self) -> AppSettings:
        """Canon: Sit/Stand on turns Display Size on (with the standing size) and Auto Zoom on."""
        af = self.auto_framing
        if af.enabled and af.sit_stand.enabled:
            self.composition.display_size = self.composition.auto_zoom = True
            self.composition.target_height = af.sit_stand.stand_size
        return self

    def problems(self, lens: Lens | None = None) -> list[str]:
        """Area rules from Canon's manual, as messages for the UI (empty = consistent).

        With a lens, also checks that the priority display and auto select areas lie inside
        the initial position's view.
        """
        out = []
        if self.priority_area.enabled and any(a.enabled for a in self.fixed_areas):
            out.append("A priority display area and fixed viewing angle areas can't be used together.")
        r = self.shooting_range
        if r.enabled:
            if self.initial.set and not (r.left <= self.initial.pan <= r.right and r.bottom <= self.initial.tilt <= r.top):
                out.append("The initial position is outside the shooting range.")
            for i, a in enumerate(self.fixed_areas):
                if a.enabled and not (r.left <= a.left and a.right <= r.right and r.bottom <= a.bottom and a.top <= r.top):
                    out.append(f"Fixed viewing angle area {i + 1} is outside the shooting range.")
        if lens and self.initial.set:
            home = view_area(self.initial, lens)
            if self.priority_area.enabled and not inside(self.priority_area, home):
                out.append("The priority display area is outside the initial position's view.")
            if self.auto_select.enabled and self.auto_select.area.enabled and not inside(self.auto_select.area, home):
                out.append("The auto select area is outside the initial position's view.")
        return out


def view_area(view: View, lens: Lens) -> Area:
    """What a viewing angle shows, as an area."""
    hfov, vfov = lens.hfov(view.zoom), lens.vfov(view.zoom)
    return Area(enabled=True, left=view.pan - hfov / 2, right=view.pan + hfov / 2,
                top=view.tilt + vfov / 2, bottom=view.tilt - vfov / 2, view=view)


def inside(a: Area, outer: Area, tolerance: float = 0.5) -> bool:
    return (outer.left - tolerance <= a.left and a.right <= outer.right + tolerance
            and outer.bottom - tolerance <= a.bottom and a.top <= outer.top + tolerance)


def merge(data: dict, patch: dict) -> None:
    """Deep-merge a partial settings change into `data` (a model_dump()).

    Lists of areas are patched by index: {"exclusion_areas": {"1": {"enabled": true}}}.
    """
    if not isinstance(patch, dict):
        raise ValueError("settings must be an object, e.g. {\"enabled\": true}")
    for key, value in patch.items():
        if key not in data:
            raise ValueError(f"unknown setting {key}")
        current = data[key]
        if isinstance(current, dict) and isinstance(value, dict):
            merge(current, value)
        elif isinstance(current, list) and isinstance(value, dict):
            for index, item in value.items():
                merge(current[int(index)], item)
        else:
            data[key] = value


def default_slots() -> list[AppSettings]:
    return [AppSettings(name=f"App Settings {i + 1}") for i in range(SLOTS)]


class PresetLink(FiniteModel):
    """Step 10: what tracking does after a camera preset is recalled."""

    auto_tracking: Literal["", "on", "off"] = ""  # "" = follow "restart after manual operation"
    slot: int = Field(0, ge=0, lt=SLOTS)


def sensitivity_gain(sensitivity: int) -> float:
    """Canon's Tracking Sensitivity 1..10 as the controller's correction gain (7 -> 1.6)."""
    return 0.4 + 0.2 * (sensitivity - 1)


# Pan/Tilt Smoothness, measured with a closed-loop harness (the real session, controller, link and VISCA code against
# a fake P400 and G2 with detector noise, head bob and sway; a person 15 m away walking, stopping and standing):
# - 1 (liveliest): no feed-forward low-pass (the raw Kalman speed), the nearest speed step, a constant acceleration of
#   the profile's max_accel, and joystick moves sent as they are. Not the motion as it was before 2026-11: following a
#   walker in one movement, the minimum dead zone, keeping up in tight shots, waiting for a walker, no turning back
#   inside the dead zone and the position moved on to now apply at every level;
# - 5 (the default) is the camera's motion profile as it stands (Motion's defaults: feed-forward low-pass 0.15 s, step
#   hysteresis 0.25): no stop-starts while a person walks, framing as good or better than before except when a person
#   stops in a tight shot (overshoot) and with a low Tracking Sensitivity in a tight shot; and a 0.22 s joystick ramp;
# - 10 (softest) low-passes the person's speed over 0.4 s, holds speed steps longer (hysteresis 0.35), builds the
#   acceleration up over 0.3 s (an S-curve) to 0.75 of max_accel, and ramps the joystick over 0.5 s. That still keeps a
#   1.2 m/s walker 15 m away in the picture at an 8-degree view (and 5), on air too; zoom-scaled acceleration did not:
#   with the on-air acceleration share it lost the walker at 5 degrees, and it smoothed nothing measurable.
SMOOTHNESS_DEFAULT = 5
SMOOTHNESS_SOFTEST = {"ff_tau": 0.4 / 0.15, "step_hysteresis": 0.35 / 0.25, "max_accel": 0.75}  # factors at 10
SMOOTHNESS_JERK_TIME = 0.3  # s of S-curve added at 10
MANUAL_RAMP_MAX = 0.5  # s: the longest a joystick speed change takes (at 10), stopping included


def smoothness_motion(motion: Motion, smoothness: int) -> Motion:
    """The motion profile at Pan/Tilt Smoothness 1..10, as factors on the camera's own profile (the on-air profile
    multiplies on top of it). Below the default the feed-forward low-pass and the step hysteresis shrink to nothing;
    above it they grow, the acceleration drops and an S-curve is added."""
    if smoothness <= SMOOTHNESS_DEFAULT:
        u = (smoothness - 1) / (SMOOTHNESS_DEFAULT - 1)  # 0 at 1, 1 at the default
        return dataclasses.replace(motion, ff_tau=motion.ff_tau * u, step_hysteresis=motion.step_hysteresis * u)
    u = (smoothness - SMOOTHNESS_DEFAULT) / (10 - SMOOTHNESS_DEFAULT)  # 0 at the default, 1 at 10
    f = {name: 1.0 + (top - 1.0) * u for name, top in SMOOTHNESS_SOFTEST.items()}
    return dataclasses.replace(motion, ff_tau=motion.ff_tau * f["ff_tau"],
                               step_hysteresis=motion.step_hysteresis * f["step_hysteresis"],
                               max_accel=motion.max_accel * f["max_accel"],
                               jerk_time=motion.jerk_time + SMOOTHNESS_JERK_TIME * u)


def manual_ramp(smoothness: int) -> float:
    """Pan/Tilt Smoothness 1..10 as the time a joystick speed change takes, at most (1: none, as before)."""
    return MANUAL_RAMP_MAX * (smoothness - 1) / 9

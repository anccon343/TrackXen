"""Canon RA-AT001-style tracking settings ("App Settings"): 5 named slots per camera.

Every area is a rectangle in room angles (pan/tilt degrees), so it stays fixed to the room
while the camera moves. Ranges follow Canon's (docs/reference.md section 3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from trackxen.control.controller import Framing
from trackxen.control.lens import Lens

SLOTS = 5
AREAS = 4  # fixed viewing angle, exclusion and halting areas each


class View(BaseModel):
    """A camera viewing angle: pan/tilt degrees and the raw zoom value."""

    set: bool = False
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0


class Area(BaseModel):
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


class ShootingRange(BaseModel):
    """Step 1: how far the camera may pan/tilt while tracking (the view edges stay inside)."""

    enabled: bool = False
    left: float = -170.0
    right: float = 170.0
    top: float = 90.0
    bottom: float = -90.0


class AutoSelect(BaseModel):
    """Step 4: a person entering the area (or the whole view) becomes the target."""

    enabled: bool = False
    start_time: float = Field(3.0, ge=0.0, le=10.0)  # seconds in the area before tracking starts
    area: Area = Field(default_factory=Area)


class MultiPerson(BaseModel):
    enabled: bool = False
    zoom_speed: int = Field(2, ge=1, le=3)
    min_size: int = Field(1, ge=0, le=3)  # higher: stop zooming out sooner
    transition_speed: int = Field(3, ge=1, le=3)  # lower: slower change between single and group


class FaceDirection(BaseModel):
    enabled: bool = False
    sensitivity: int = Field(2, ge=1, le=3)
    space: int = Field(2, ge=1, le=3)  # how much room to leave in the facing direction
    forward: Literal["maintain", "center"] = "maintain"


class SitStand(BaseModel):
    enabled: bool = False
    sit_size: float = Field(0.7, ge=0.2, le=0.95)  # person height in the frame when sitting
    stand_size: float = Field(0.5, ge=0.2, le=0.95)  # ... when standing


class OnAir(BaseModel):
    """Motion while the camera is on Program (vMix tally, or the manual on-air switch): calmer.
    Blended in and out over about a second, so the switch itself doesn't show."""

    enabled: bool = True
    speed: float = Field(0.5, ge=0.1, le=1.0)  # share of the normal top speed
    accel: float = Field(0.35, ge=0.1, le=1.0)  # share of the normal acceleration: softer starts and stops
    dead_zone: float = Field(1.5, ge=1.0, le=3.0)  # the Pan/Tilt Halting Area grows by this
    zoom: Literal["slow", "off"] = "slow"
    retarget: Literal["wait", "slow"] = "wait"  # a new target while live: wait until off Program, or go slowly


class AutoFraming(BaseModel):
    """Step 8."""

    enabled: bool = False
    multi_person: MultiPerson = Field(default_factory=MultiPerson)
    face_direction: FaceDirection = Field(default_factory=FaceDirection)
    sit_stand: SitStand = Field(default_factory=SitStand)


def _areas() -> list[Area]:
    return [Area() for _ in range(AREAS)]


class AppSettings(BaseModel):
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


class PresetLink(BaseModel):
    """Step 10: what tracking does after a camera preset is recalled."""

    auto_tracking: Literal["", "on", "off"] = ""  # "" = follow "restart after manual operation"
    slot: int = Field(0, ge=0, lt=SLOTS)


def sensitivity_gain(sensitivity: int) -> float:
    """Canon's Tracking Sensitivity 1..10 as the controller's correction gain (7 -> 1.6)."""
    return 0.4 + 0.2 * (sensitivity - 1)

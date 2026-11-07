"""Area rules: who may be auto-selected, where tracking halts or the view is fixed,
and how the shooting range and priority display area limit where the camera points.

Precedence: exclusion beats auto select; tracking halting beats fixed, auto
select and exclusion; shooting range and priority display area beat auto framing.
"""

from __future__ import annotations

from dataclasses import dataclass

from trackxen.control.camera import Pose
from trackxen.control.controller import head_point
from trackxen.control.lens import Lens
from trackxen.core.tracking_settings import AppSettings, Area, View


@dataclass
class Placed:
    """A tracked person in the room: head direction, box edges (pan) and height, all in degrees."""

    id: int
    pan: float
    tilt: float
    track: dict
    left: float = 0.0
    right: float = 0.0
    height: float = 0.0


def place(msg: dict, pose: Pose, lens: Lens) -> list[Placed]:
    out = []
    w, h = msg["width"], msg["height"]
    for t in msg["tracks"]:
        if t["missed"]:
            continue
        x, y = head_point(t["box"], t["keypoints"])
        dpan, dtilt = lens.pixel_to_angles(x, y, w, h, pose.zoom)
        x1, y1, x2, y2 = t["box"]
        left = lens.pixel_to_angles(x1, (y1 + y2) / 2, w, h, pose.zoom)[0]
        right = lens.pixel_to_angles(x2, (y1 + y2) / 2, w, h, pose.zoom)[0]
        top = lens.pixel_to_angles((x1 + x2) / 2, y1, w, h, pose.zoom)[1]
        bottom = lens.pixel_to_angles((x1 + x2) / 2, y2, w, h, pose.zoom)[1]
        out.append(Placed(t["id"], pose.pan + dpan, pose.tilt + dtilt, t,
                          pose.pan + left, pose.pan + right, top - bottom))
    return out


def index_in(areas: list[Area], p: Placed) -> int | None:
    """Number (0-based) of the first area the person's head is in."""
    return next((i for i, a in enumerate(areas) if a.contains(p.pan, p.tilt)), None)


def halting_area_of(slot: AppSettings, p: Placed) -> int | None:
    return index_in(slot.halting_areas, p)


def fixed_area_of(slot: AppSettings, p: Placed) -> int | None:
    if slot.priority_area.enabled:  # the two can't be combined; priority wins
        return None
    return index_in(slot.fixed_areas, p)


def zone_of(slot: AppSettings, p: Placed) -> str:
    """Why a person can't be picked: "halting" (nobody is selected there, not even by a
    click), "excluded" (never auto-picked), or "" (selectable)."""
    if index_in(slot.halting_areas, p) is not None:
        return "halting"
    if index_in(slot.exclusion_areas, p) is not None:
        return "excluded"
    return ""


def selectable(slot: AppSettings, p: Placed) -> bool:
    """May this person be picked automatically?"""
    if zone_of(slot, p):
        return False
    area = slot.auto_select.area
    return not (slot.auto_select.enabled and area.enabled) or area.contains(p.pan, p.tilt)


class AutoSelector:
    """Picks the person who has stayed in the auto select area for `start_time` seconds
    (the one who has been there longest, if several qualify)."""

    def __init__(self):
        self.since: dict[int, float] = {}

    def reset(self) -> None:
        self.since.clear()

    def update(self, slot: AppSettings, people: list[Placed], now: float) -> int | None:
        present = {p.id for p in people if selectable(slot, p)}
        self.since = {pid: t for pid, t in self.since.items() if pid in present}
        for pid in present:
            self.since.setdefault(pid, now)
        ready = [(t, pid) for pid, t in self.since.items() if now - t >= slot.auto_select.start_time]
        return min(ready)[1] if ready else None

    def progress(self, slot: AppSettings, now: float) -> dict[int, float]:
        """Seconds each candidate still has to wait (for the UI)."""
        return {pid: max(0.0, slot.auto_select.start_time - (now - t)) for pid, t in self.since.items()}


def clamp_to_range(slot: AppSettings, pan: float, tilt: float, hfov: float, vfov: float) -> tuple[float, float]:
    """Keep the picture's edges inside the shooting range (Pan/Tilt Operation Control)."""
    r = slot.shooting_range
    if not r.enabled:
        return pan, tilt
    lo, hi = r.left + hfov / 2, r.right - hfov / 2
    pan = (r.left + r.right) / 2 if lo > hi else min(hi, max(lo, pan))
    lo, hi = r.bottom + vfov / 2, r.top - vfov / 2
    tilt = (r.top + r.bottom) / 2 if lo > hi else min(hi, max(lo, tilt))
    return pan, tilt


def keep_priority_in_view(slot: AppSettings, pan: float, tilt: float, hfov: float, vfov: float,
                          margin: float = 0.05) -> tuple[float, float]:
    """Move the aim just enough that the priority display area stays in the picture (step 3)."""
    a = slot.priority_area
    if not a.enabled:
        return pan, tilt
    mh, mv = hfov * margin, vfov * margin
    lo, hi = a.right + mh - hfov / 2, a.left - mh + hfov / 2  # pan range keeping the area inside
    pan = (a.left + a.right) / 2 if lo > hi else min(hi, max(lo, pan))
    lo, hi = a.top + mv - vfov / 2, a.bottom - mv + vfov / 2
    tilt = (a.top + a.bottom) / 2 if lo > hi else min(hi, max(lo, tilt))
    return pan, tilt


def view_for_area(area: Area, lens: Lens, margin: float = 1.15) -> View:
    """The camera angle an area was drawn at, or else one that shows the whole area."""
    if area.view.set:
        return area.view
    hfov = max((area.right - area.left) * margin, lens.hfov_for_vfov((area.top - area.bottom) * margin))
    pan, tilt = area.center
    return View(set=True, pan=pan, tilt=tilt, zoom=lens.zoom_for_hfov(hfov))


def max_zoom_for_priority(slot: AppSettings, lens: Lens, margin: float = 0.05) -> float | None:
    """The most the camera may zoom in and still show the whole priority display area."""
    a = slot.priority_area
    if not a.enabled:
        return None
    width = (a.right - a.left) * (1 + 2 * margin)
    height = (a.top - a.bottom) * (1 + 2 * margin)
    return lens.zoom_for_hfov(max(width, lens.hfov_for_vfov(height)))

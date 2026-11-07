"""Between the picture (0..1 coordinates) and room angles (pan/tilt degrees).

Areas are stored in room angles so they stay fixed to the room while the camera moves; the
UI draws them by projecting them back into the current picture.
"""

from __future__ import annotations

from trackxen.control.camera import Pose
from trackxen.control.lens import Lens
from trackxen.core.tracking_settings import Area

_W = 1600.0  # any width works; only the aspect ratio matters


def _size(lens: Lens) -> tuple[float, float]:
    return _W, _W / lens.aspect


def view_to_room(x: float, y: float, pose: Pose, lens: Lens) -> tuple[float, float]:
    w, h = _size(lens)
    dpan, dtilt = lens.pixel_to_angles(x * w, y * h, w, h, pose.zoom)
    return pose.pan + dpan, pose.tilt + dtilt


def room_to_view(pan: float, tilt: float, pose: Pose, lens: Lens) -> tuple[float, float]:
    w, h = _size(lens)
    px, py = lens.angles_to_pixel(pan - pose.pan, tilt - pose.tilt, w, h, pose.zoom)
    return px / w, py / h


def area_from_view(x1: float, y1: float, x2: float, y2: float, pose: Pose, lens: Lens) -> Area:
    """A rectangle drawn on the picture, as a room-angle area."""
    corners = [view_to_room(x, y, pose, lens) for x in (x1, x2) for y in (y1, y2)]
    pans, tilts = [c[0] for c in corners], [c[1] for c in corners]
    return Area(enabled=True, left=min(pans), right=max(pans), top=max(tilts), bottom=min(tilts))


def area_to_view(area: Area, pose: Pose, lens: Lens, reach: float = 80.0) -> tuple[float, float, float, float] | None:
    """The area in the current picture (may reach outside 0..1); None when it is out of sight.

    The flat-picture projection breaks down towards 90 degrees, so the area is first cut to
    `reach` degrees around the view direction; cut edges land far outside the picture.
    """
    left, right = max(area.left, pose.pan - reach), min(area.right, pose.pan + reach)
    bottom, top = max(area.bottom, pose.tilt - reach), min(area.top, pose.tilt + reach)
    if left >= right or bottom >= top:
        return None
    x1, y1 = room_to_view(left, top, pose, lens)
    x2, y2 = room_to_view(right, bottom, pose, lens)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def view_edges(pose: Pose, lens: Lens) -> dict[str, float]:
    """Room angles of the current picture's edges (for capturing a shooting range edge)."""
    hfov, vfov = lens.hfov(pose.zoom), lens.vfov(pose.zoom)
    return {"left": pose.pan - hfov / 2, "right": pose.pan + hfov / 2,
            "top": pose.tilt + vfov / 2, "bottom": pose.tilt - vfov / 2}

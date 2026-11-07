"""Zoom position <-> field of view, and image pixels <-> angles.

The field of view shrinks exponentially with zoom position, which matches typical PTZ
blocks closely. Defaults are a Sony-style 20x block (wide ~60 degrees, zoom 0..16384);
each real camera's curve gets measured in calibration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Lens:
    hfov_wide: float = 60.0  # degrees at zoom 0
    hfov_tele: float = 3.2  # degrees at zoom_max
    zoom_max: float = 16384.0
    aspect: float = 16 / 9

    def hfov(self, zoom: float) -> float:
        z = min(1.0, max(0.0, zoom / self.zoom_max))
        return self.hfov_wide * (self.hfov_tele / self.hfov_wide) ** z

    def vfov(self, zoom: float) -> float:
        return math.degrees(2 * math.atan(math.tan(math.radians(self.hfov(zoom) / 2)) / self.aspect))

    def hfov_for_vfov(self, vfov: float) -> float:
        vfov = min(vfov, 179.9)  # (tan() wraps negative at 180 degrees: a view that tall is the widest there is)
        return math.degrees(2 * math.atan(math.tan(math.radians(vfov / 2)) * self.aspect))

    def zoom_for_hfov(self, hfov: float) -> float:
        hfov = min(self.hfov_wide, max(self.hfov_tele, hfov))
        return self.zoom_max * math.log(hfov / self.hfov_wide) / math.log(self.hfov_tele / self.hfov_wide)

    def focal_px(self, width: int, zoom: float) -> float:
        """Focal length in pixels for an image `width` pixels wide."""
        return (width / 2) / math.tan(math.radians(self.hfov(zoom) / 2))

    def pixel_to_angles(self, x: float, y: float, width: int, height: int, zoom: float) -> tuple[float, float]:
        """Angles (pan right +, tilt up +) of an image point relative to the image centre."""
        f = self.focal_px(width, zoom)
        return math.degrees(math.atan((x - width / 2) / f)), -math.degrees(math.atan((y - height / 2) / f))

    def angles_to_pixel(self, dpan: float, dtilt: float, width: int, height: int, zoom: float) -> tuple[float, float]:
        f = self.focal_px(width, zoom)
        return width / 2 + f * math.tan(math.radians(dpan)), height / 2 - f * math.tan(math.radians(dtilt))

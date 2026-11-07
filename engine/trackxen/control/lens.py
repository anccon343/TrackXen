"""Zoom position <-> field of view, and image pixels <-> angles.

Without measurements the field of view shrinks exponentially with zoom position between the two
ends (a Sony-style 20x block by default: wide ~60 degrees, zoom 0..16384). Real blocks differ a lot
in the middle: the PTZOptics G2 12X is 28 degrees wide at half zoom where that curve says 20.5. So a
camera type can carry a measured zoom table (tools/lens_probe.py): between its points the focal
length (1 / tan(hfov / 2)) changes evenly on a log scale.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, field


def _log_half_tan(hfov: float) -> float:
    return math.log(math.tan(math.radians(hfov / 2)))


def _hfov_from_log_half_tan(value: float) -> float:
    return 2 * math.degrees(math.atan(math.exp(value)))


@dataclass
class Lens:
    hfov_wide: float = 60.0  # degrees at zoom 0
    hfov_tele: float = 3.2  # degrees at zoom_max
    zoom_max: float = 16384.0
    aspect: float = 16 / 9
    # Measured (zoom position, horizontal field of view) points from zoom 0 up, narrower each time.
    # When given, its ends are hfov_wide, hfov_tele and zoom_max.
    zoom_table: Sequence[tuple[float, float]] = ()
    _zooms: list[float] = field(default_factory=list, init=False, repr=False, compare=False)
    _logs: list[float] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.zoom_table:
            self.zoom_table = tuple((float(z), float(h)) for z, h in self.zoom_table)
            self._zooms = [z for z, _ in self.zoom_table]
            self._logs = [_log_half_tan(h) for _, h in self.zoom_table]  # (falling: narrower each point)
            self.hfov_wide, self.hfov_tele = self.zoom_table[0][1], self.zoom_table[-1][1]
            self.zoom_max = self.zoom_table[-1][0]

    @classmethod
    def from_config(cls, cfg) -> Lens:
        """The lens of a camera's settings (core/config.py LensConfig)."""
        return cls(cfg.hfov_wide, cfg.hfov_tele, cfg.zoom_max, zoom_table=tuple(map(tuple, cfg.zoom_table)))

    def hfov(self, zoom: float) -> float:
        if self._zooms:
            if zoom <= self._zooms[0]:
                return self.hfov_wide
            if zoom >= self._zooms[-1]:
                return self.hfov_tele
            i = bisect.bisect_right(self._zooms, zoom)  # zoom lies between points i-1 and i
            z0, z1 = self._zooms[i - 1], self._zooms[i]
            share = (zoom - z0) / (z1 - z0)
            return _hfov_from_log_half_tan(self._logs[i - 1] + share * (self._logs[i] - self._logs[i - 1]))
        z = min(1.0, max(0.0, zoom / self.zoom_max))
        return self.hfov_wide * (self.hfov_tele / self.hfov_wide) ** z

    def vfov(self, zoom: float) -> float:
        return math.degrees(2 * math.atan(math.tan(math.radians(self.hfov(zoom) / 2)) / self.aspect))

    def hfov_for_vfov(self, vfov: float) -> float:
        vfov = min(vfov, 179.9)  # (tan() wraps negative at 180 degrees: a view that tall is the widest there is)
        return math.degrees(2 * math.atan(math.tan(math.radians(vfov / 2)) * self.aspect))

    def zoom_for_hfov(self, hfov: float) -> float:
        hfov = min(self.hfov_wide, max(self.hfov_tele, hfov))
        if self._zooms:
            value = _log_half_tan(hfov)
            for i in range(1, len(self._zooms)):  # the first point at or past the wanted field of view
                if value >= self._logs[i]:
                    l0, l1 = self._logs[i - 1], self._logs[i]
                    share = (l0 - value) / (l0 - l1)
                    return self._zooms[i - 1] + share * (self._zooms[i] - self._zooms[i - 1])
            return self.zoom_max
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

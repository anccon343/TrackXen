"""TrackXen's mark: a red triangle pointing up, with sharp corners. One drawing for the program's icon
(packaging/make_icon.py) and the tray icon (desktop.py); the web UI draws the same triangle in SVG
(ui/index.html and TopBar)."""

from __future__ import annotations

RED = (224, 36, 43)  # the brand red, #e0242b


def mark(size: int):
    """The triangle as a size x size RGBA image, lit from the top: lighter red at its tip, deep red at its base."""
    import numpy as np
    from PIL import Image, ImageDraw

    s = size * 4  # drawn 4x larger and scaled down: smooth edges
    w = s * 0.94
    h = w * 3 ** 0.5 / 2
    top, cx = (s - h) / 2, s / 2
    shape = Image.new("L", (s, s), 0)
    ImageDraw.Draw(shape).polygon([(cx, top), (cx + w / 2, top + h), (cx - w / 2, top + h)], fill=255)
    y, x = np.mgrid[0:s, 0:s] + 0.5
    t = np.clip((y - top) / h * 0.8 + (x - cx) / w * 0.35, 0.0, 1.0)[..., None]
    light, mid, dark = np.array([255, 105, 92.0]), np.array(RED, float), np.array([128, 4, 16.0])
    rgb = np.where(t < 0.4, light + (mid - light) * (t / 0.4), mid + (dark - mid) * ((t - 0.4) / 0.6))
    img = np.dstack([np.clip(rgb, 0, 255), np.asarray(shape, float)]).astype(np.uint8)
    return Image.fromarray(img, "RGBA").resize((size, size), Image.LANCZOS)

"""TrackXen's mark: a big red ball. One drawing for the program's icon (packaging/make_icon.py) and the
tray icon (desktop.py); the web UI draws the same ball in SVG (ui/index.html) and CSS (TopBar)."""

from __future__ import annotations

RED = (224, 36, 43)  # the brand red, #e0242b


def red_ball(size: int):
    """The ball as a size x size RGBA image: a glossy red sphere lit from the upper left."""
    import numpy as np
    from PIL import Image

    s = size * 4  # drawn 4x larger and scaled down: smooth edges
    y, x = np.mgrid[0:s, 0:s] + 0.5
    r = s * 0.47
    dx, dy = (x - s / 2) / r, (y - s / 2) / r
    dist = np.sqrt(dx * dx + dy * dy)
    # body: light red near the lit side, deep red at the far edge
    t = np.clip(np.hypot(dx + 0.38, dy + 0.42) / 1.5, 0.0, 1.0)[..., None]
    light, mid, dark = np.array([255, 120, 105.0]), np.array(RED, float), np.array([110, 0, 12.0])
    body = np.where(t < 0.45, light + (mid - light) * (t / 0.45), mid + (dark - mid) * ((t - 0.45) / 0.55))
    # a soft highlight
    spot = np.clip(1.0 - np.hypot((dx + 0.33) / 0.42, (dy + 0.4) / 0.3), 0.0, 1.0)[..., None] ** 1.6
    rgb = body + (255.0 - body) * spot * 0.85
    alpha = np.clip((1.0 - dist) * r, 0.0, 1.0) * 255  # 1 inside, a pixel-wide edge
    img = np.dstack([np.clip(rgb, 0, 255), alpha]).astype(np.uint8)
    return Image.fromarray(img, "RGBA").resize((size, size), Image.LANCZOS)

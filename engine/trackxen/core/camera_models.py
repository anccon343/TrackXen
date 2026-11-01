"""The camera types TrackXen knows, and the settings each one starts with.

Picking a type in Settings -> Cameras (or adding a camera by its IP address) fills these in; every
value can still be changed per camera. Values marked `estimate` come from the data sheet and are
measured on the real camera with the lab tools: speeds, units per degree and zoom range with
tools/visca_probe.py, the video delay with tools/stream_probe.py.
"""

from __future__ import annotations

from trackxen.control.controller import DEFAULT_PAN_SPEEDS, DEFAULT_TILT_SPEEDS


def speed_curve(slowest: float, fastest: float, steps: int) -> list[float]:
    """Degrees per second for each VISCA speed step, evenly spaced on a log scale (an estimate
    from the data sheet's slowest and fastest speeds, until the speed sweep measures them)."""
    return [round(slowest * (fastest / slowest) ** (i / (steps - 1)), 2) for i in range(steps)]


MODELS: dict[str, dict] = {
    "birddog_p400": {
        "name": "BirdDog P400",
        "about": "NDI video, FreeD positions, Sony VISCA over IP (UDP 52381).",
        "visca_transport": "sony", "visca_port": 52381, "freed": True, "video": "ndi",
        "units_per_degree": 16.0, "tilt_units_per_degree": None,
        "pan_speeds": list(DEFAULT_PAN_SPEEDS), "tilt_speeds": list(DEFAULT_TILT_SPEEDS),
        "lens": {"hfov_wide": 60.0, "hfov_tele": 3.2, "zoom_max": 16384.0},
        "video_delay": 0.08,
        "estimate": ["units_per_degree", "pan_speeds", "tilt_speeds", "video_delay"],
    },
    "ptzoptics_g2": {
        "name": "PTZOptics G2 (e.g. PT20X-SDI-GY-G2)",
        "about": "Video as an RTSP network stream (rtsp://IP:554/1, sub stream /2); plain VISCA on UDP 1259 "
                 "(TCP 5678 also works, but the camera takes only one TCP connection); no FreeD, so the "
                 "position is asked over VISCA 20 times a second.",
        "visca_transport": "udp", "visca_port": 1259, "freed": False, "video": "stream",
        "stream_path": "/1", "stream_transport": "tcp",
        # data sheet: pan ±170°, tilt -30°..+90°; the VISCA units for those ends are measured (calibrate)
        "units_per_degree": 14.4, "tilt_units_per_degree": 13.33,
        "pan_speeds": speed_curve(1.7, 100.0, 24),  # 24 pan speed steps, 1.7-100°/s (data sheet)
        "tilt_speeds": speed_curve(1.7, 69.9, 20),  # 20 tilt speed steps (VISCA 0x01-0x14), up to 69.9°/s
        "lens": {"hfov_wide": 60.7, "hfov_tele": 3.36, "zoom_max": 16384.0},
        "video_delay": 0.2,
        "estimate": ["units_per_degree", "tilt_units_per_degree", "pan_speeds", "tilt_speeds", "lens.zoom_max",
                     "video_delay"],
    },
    "other": {
        "name": "Other VISCA camera",
        "about": "Any PTZ camera with VISCA over IP. Fill in its ports and measure it with the lab tools.",
        "visca_transport": "sony", "visca_port": 52381, "freed": False, "video": "ndi",
        "units_per_degree": 16.0, "tilt_units_per_degree": None,
        "pan_speeds": list(DEFAULT_PAN_SPEEDS), "tilt_speeds": list(DEFAULT_TILT_SPEEDS)[:20],
        "lens": {"hfov_wide": 60.0, "hfov_tele": 3.2, "zoom_max": 16384.0},
        "video_delay": 0.15,
        "estimate": ["units_per_degree", "pan_speeds", "tilt_speeds", "lens", "video_delay"],
    },
}

CAMERA_FIELDS = ("visca_transport", "visca_port", "units_per_degree", "tilt_units_per_degree", "pan_speeds",
                 "tilt_speeds", "lens", "video_delay", "video", "stream_transport")


def camera_defaults(model: str, number: int = 1) -> dict:
    """CameraConfig fields for a new camera of this type (camera `number`: its FreeD port)."""
    m = MODELS[model]
    fields = {k: (dict(m[k]) if isinstance(m[k], dict) else list(m[k]) if isinstance(m[k], list) else m[k])
              for k in CAMERA_FIELDS if k in m}
    fields.update(model=model, freed_port=40000 + number if m["freed"] else None, stream_url="")
    return fields


def stream_url(model: str, host: str, sub_stream: bool = False) -> str:
    """The camera's own RTSP address, e.g. rtsp://192.168.1.60:554/1 (PTZOptics main stream)."""
    path = "/2" if sub_stream else MODELS.get(model, {}).get("stream_path", "/1")
    return f"rtsp://{host}:554{path}" if host else ""

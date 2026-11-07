"""The camera types TrackXen knows, and the settings each one starts with.

Picking a type in Settings -> Cameras (or adding a camera by its IP address) fills these in; every
value can still be changed per camera. Values marked `estimate` come from the data sheet and are
measured on the real camera with the lab tools: speeds, units per degree and zoom range with
tools/visca_probe.py, the field of view by zoom and the video delay with tools/lens_probe.py
(network cameras) or tools/stream_probe.py.
"""

from __future__ import annotations

import copy

from trackxen.control.controller import DEFAULT_PAN_SPEEDS, DEFAULT_TILT_SPEEDS


def speed_curve(slowest: float, fastest: float, steps: int) -> list[float]:
    """Degrees per second for each VISCA speed step, evenly spaced on a log scale (an estimate
    from the data sheet's slowest and fastest speeds, until the speed sweep measures them)."""
    return [round(slowest * (fastest / slowest) ** (i / (steps - 1)), 2) for i in range(steps)]


# PTZOptics G2 (PT12X-SDI-G2, PT20X-SDI-G2), measured on a PT12X-SDI-WH-G2 and a PT20X-SDI-GY-G2 on 2026-11-07
# (docs/development/progress.md). Both models stop at pan -2448..2448 and tilt -432..1296 VISCA units (±170°, -30°..+90°), i.e. 14.4
# units per degree on both axes. Their speed steps are nearly alike; the fastest steps read a little low, because
# the sweep includes the acceleration. The video delay holds for the stream settings in "about": at the camera's
# 12 Mbps its stream falls 1-3 s behind, and its VISCA answers slow down to ~0.55 s.
_G2 = {
    "about": "Video as an RTSP network stream (rtsp://IP:554/1, sub stream /2); plain VISCA on UDP 1259 "
             "(TCP 5678 also works, but the camera takes only one TCP connection); no FreeD, so the "
             "position is asked over VISCA 20 times a second. Set the camera's main stream to H.264 1080p25 "
             "(1080p30 where the mains are 60 Hz), 4 Mbps CBR, I-frame interval 25 (30): at 12 Mbps the camera "
             "falls seconds behind.",
    "visca_transport": "udp", "visca_port": 1259, "freed": False, "video": "stream",
    "stream_path": "/1", "stream_transport": "tcp",
    "units_per_degree": 14.4, "tilt_units_per_degree": None,  # (tilt: the same 14.4)
    "video_delay": 0.10,  # 4 Mbps CBR: ~0.1 s from the position readback moving to the picture moving
}

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
    "ptzoptics_g2": {  # (the key from before the 12X had its own type: saved settings keep working)
        **_G2,
        "name": "PTZOptics G2 20X (e.g. PT20X-SDI-GY-G2)",
        # degrees per second for each VISCA speed step: 24 pan steps, 20 tilt steps (0x01-0x14)
        "pan_speeds": [1.74, 2.29, 3.13, 4.31, 6.04, 7.92, 9.93, 11.81, 14.72, 17.29, 20.49, 23.68, 27.08, 30.83,
                       34.86, 39.03, 43.47, 48.26, 52.99, 58.19, 62.78, 66.81, 71.67, 75.07],
        "tilt_speeds": [1.82, 2.34, 3.21, 4.34, 6.08, 7.99, 9.46, 11.81, 13.98, 16.32, 18.92, 20.66, 24.39, 27.34,
                        28.73, 34.03, 37.41, 41.15, 44.88, 48.87],
        # zoom ends at 15070, not 16384. The full-tele field of view rests on few matched points (the picture
        # didn't focus on the near wall it was measured on): to check on a far target.
        "lens": {"hfov_wide": 58.22, "hfov_tele": 3.65, "zoom_max": 15070.0,
                 "zoom_table": [[0, 58.22], [1882, 48.44], [3767, 39.26], [5650, 30.71], [7535, 22.89],
                                [9417, 16.11], [11302, 10.55], [13185, 6.45], [15070, 3.65]]},
        "estimate": ["lens (full tele)"],
    },
    "ptzoptics_g2_12x": {
        **_G2,
        "name": "PTZOptics G2 12X (e.g. PT12X-SDI-WH-G2)",
        "pan_speeds": [1.74, 2.29, 3.19, 4.38, 6.11, 7.85, 9.93, 12.22, 14.79, 17.43, 20.35, 23.4, 26.74, 30.21,
                       33.82, 37.71, 41.74, 46.11, 50.35, 55.0, 58.96, 62.78, 66.94, 70.14],
        "tilt_speeds": [1.82, 2.34, 3.21, 4.34, 6.08, 7.73, 9.72, 11.2, 13.98, 16.23, 18.66, 21.09, 23.61, 26.22,
                        28.91, 31.77, 34.55, 37.5, 40.45, 43.49],
        "lens": {"hfov_wide": 66.67, "hfov_tele": 6.31, "zoom_max": 16384.0,
                 "zoom_table": [[0, 66.67], [2048, 56.5], [4096, 46.51], [6144, 36.9], [8192, 28.01],
                                [10240, 20.06], [12288, 13.32], [14336, 7.98], [16384, 6.31]]},
        "estimate": [],
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
    fields = {k: copy.deepcopy(m[k]) for k in CAMERA_FIELDS if k in m}  # (each camera its own lists and tables)
    fields.update(model=model, freed_port=40000 + number if m["freed"] else None, stream_url="")
    return fields


def stream_url(model: str, host: str, sub_stream: bool = False) -> str:
    """The camera's own RTSP address, e.g. rtsp://192.168.1.60:554/1 (PTZOptics main stream)."""
    path = "/2" if sub_stream else MODELS.get(model, {}).get("stream_path", "/1")
    return f"rtsp://{host}:554{path}" if host else ""

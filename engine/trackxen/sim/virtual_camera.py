"""A virtual P400: the fake camera's VISCA + FreeD, plus live video of a stage as an NDI source.

The stage is a wide canvas in room angles (pan/tilt degrees). People are photo cut-outs
from the sample photo shipped with TrackXen, so the real detector sees real people. The picture is
rendered with the camera's position from `video_delay` seconds ago, like a real video
pipeline, and commands take effect after `command_delay`.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from trackxen.control.lens import Lens
from trackxen.sim.fake_camera import FakeCamera


@dataclass
class Actor:
    sprite: np.ndarray  # BGR photo of a person, cropped to the person
    height_deg: float = 7.0  # about 1.8 m seen from ~15 m
    feet_tilt: float = -8.0
    waypoints: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 1e9)])  # (pan, dwell s)
    speed: float = 4.0  # walking speed in degrees per second
    _timeline: list[tuple[float, float, float, float]] = field(default_factory=list, repr=False)

    def __post_init__(self):
        t, pan = 0.0, self.waypoints[0][0]
        for target, dwell in self.waypoints[1:] + self.waypoints[:1]:
            travel = abs(target - pan) / self.speed
            self._timeline.append((t, travel, pan, target))
            t += travel
            self._timeline.append((t, dwell, target, target))
            t += dwell
            pan = target
        self._period = t

    def pan_at(self, t: float) -> float:
        t = t % self._period if self._period > 0 else 0.0
        for start, duration, a, b in self._timeline:
            if t < start + duration:
                k = (t - start) / duration if duration > 0 else 1.0
                return a + (b - a) * k
        return self._timeline[-1][3]


def sample_photo() -> np.ndarray:
    """The people photo shipped with TrackXen (core/assets/people.jpg: Ultralytics' bus.jpg)."""
    from trackxen.core.hwtest import PEOPLE_PHOTO

    return cv2.imread(str(PEOPLE_PHOTO))


def person_sprites(model_path: Path, count: int = 3) -> list[np.ndarray]:
    """Crop the tallest people out of the sample photo with our own detector."""
    from trackxen.vision.yolo_pose import PoseDetector

    image = sample_photo()
    people = PoseDetector(model_path, engines=("cpu",), conf=0.5).detect([image])[0]
    people.sort(key=lambda p: p.box[3] - p.box[1], reverse=True)
    sprites = []
    h, w = image.shape[:2]
    for p in people[:count]:
        x1, y1, x2, y2 = (int(v) for v in p.box)
        pad = int((x2 - x1) * 0.08)
        sprites.append(image[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)].copy())
    if not sprites:
        raise RuntimeError("no people found in the sample image")
    return sprites


class Scene:
    """A stage in room angles: pan pan_min..pan_max, tilt tilt_min..tilt_max, `ppd` pixels per degree."""

    def __init__(self, actors: list[Actor], ppd: float = 40.0, pan_range=(-60.0, 60.0),
                 tilt_range=(-25.0, 20.0)):
        self.actors, self.ppd = actors, ppd
        self.pan_min, self.pan_max = pan_range
        self.tilt_min, self.tilt_max = tilt_range
        self.background = self._draw_background()
        self._scaled = []
        for a in actors:
            h = max(8, round(a.height_deg * ppd))
            w = max(4, round(a.sprite.shape[1] * h / a.sprite.shape[0]))
            self._scaled.append(cv2.resize(a.sprite, (w, h), interpolation=cv2.INTER_AREA))

    def to_canvas(self, pan: float, tilt: float) -> tuple[float, float]:
        return (pan - self.pan_min) * self.ppd, (self.tilt_max - tilt) * self.ppd

    def _draw_background(self) -> np.ndarray:
        w = round((self.pan_max - self.pan_min) * self.ppd)
        h = round((self.tilt_max - self.tilt_min) * self.ppd)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        rows = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        img[:] = (np.array([45, 30, 28], np.float32) * (1 - rows) + np.array([20, 18, 18], np.float32) * rows)[:, None]
        def rect(p1, t1, p2, t2, color):
            x1, y1 = self.to_canvas(p1, t1)
            x2, y2 = self.to_canvas(p2, t2)
            cv2.rectangle(img, (round(x1), round(y1)), (round(x2), round(y2)), color, -1)
        rect(-60, -8, 60, -25, (35, 45, 60))  # stage floor
        rect(-14, 12, 14, -1, (200, 190, 175))  # projection screen
        rect(-12, 10, 12, 1, (150, 120, 80))  # slide on the screen
        rect(-2.5, -3.5, 2.5, -8, (40, 70, 110))  # podium
        rect(-60, -14, 60, -25, (25, 25, 30))  # front rows (dark)
        for pan in range(-55, 60, 10):  # pillars for visible motion
            rect(pan, 20, pan + 1.2, -8, (60, 55, 50))
        x, y = self.to_canvas(-12, 8)
        cv2.putText(img, "SUNDAY SERVICE", (round(x), round(y)), cv2.FONT_HERSHEY_SIMPLEX, self.ppd / 20,
                    (255, 255, 255), max(1, round(self.ppd / 15)))
        return img

    def render(self, pan: float, tilt: float, hfov: float, t: float,
               size: tuple[int, int] = (1920, 1080)) -> np.ndarray:
        out_w, out_h = size
        vfov = hfov * out_h / out_w
        cx, cy = self.to_canvas(pan, tilt)
        cw, ch = hfov * self.ppd, vfov * self.ppd
        x0, y0 = math.floor(cx - cw / 2), math.floor(cy - ch / 2)
        x1, y1 = math.ceil(cx + cw / 2), math.ceil(cy + ch / 2)
        crop = np.full((y1 - y0, x1 - x0, 3), 12, dtype=np.uint8)
        H, W = self.background.shape[:2]
        sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if sx1 > sx0 and sy1 > sy0:
            crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = self.background[sy0:sy1, sx0:sx1]
        interp = cv2.INTER_AREA if crop.shape[1] > out_w else cv2.INTER_LINEAR
        out = cv2.resize(crop, (out_w, out_h), interpolation=interp)
        # People are drawn from their original photos at screen resolution, so zooming in on a
        # face shows real detail (the stage canvas would only have a blur of a few pixels)
        sppd = out_w / hfov  # screen pixels per degree
        left, top = pan - hfov / 2, tilt + vfov / 2
        for actor, small in zip(self.actors, self._scaled):
            h = round(actor.height_deg * sppd)
            w = round(actor.sprite.shape[1] * h / actor.sprite.shape[0])
            ax0 = round((actor.pan_at(t) - left) * sppd - w / 2)
            ay0 = round((top - actor.feet_tilt) * sppd - h)
            px0, py0, px1, py1 = max(ax0, 0), max(ay0, 0), min(ax0 + w, out_w), min(ay0 + h, out_h)
            if px1 <= px0 or py1 <= py0 or w < 2 or h < 2:
                continue
            src = small if h <= small.shape[0] else actor.sprite  # far away: the pre-shrunk copy is enough
            scale_x, scale_y = src.shape[1] / w, src.shape[0] / h
            part = src[int((py0 - ay0) * scale_y):max(int((py0 - ay0) * scale_y) + 1, int(np.ceil((py1 - ay0) * scale_y))),
                       int((px0 - ax0) * scale_x):max(int((px0 - ax0) * scale_x) + 1, int(np.ceil((px1 - ax0) * scale_x)))]
            out[py0:py1, px0:px1] = cv2.resize(part, (px1 - px0, py1 - py0),
                                               interpolation=cv2.INTER_AREA if part.shape[0] > py1 - py0 else cv2.INTER_LINEAR)
        return out


def default_scene(model_path: Path, ppd: float = 40.0) -> Scene:
    sprites = person_sprites(model_path)
    speaker = Actor(sprites[0], waypoints=[(-22.0, 3.0), (0.0, 6.0), (20.0, 4.0), (6.0, 2.0)], speed=4.0)
    actors = [speaker]
    if len(sprites) > 1:  # someone standing still at the side of the stage
        actors.append(Actor(sprites[1], height_deg=6.8, waypoints=[(38.0, 1e9)]))
    return Scene(actors, ppd=ppd)


class VirtualCamera:
    """Runs a FakeCamera (VISCA + FreeD) and sends its view of `scene` as an NDI source.
    `camera_args` change the FakeCamera, e.g. fake_camera.ptzoptics_settings() for a PTZOptics G2."""

    def __init__(self, scene: Scene, ndi_name: str = "TRACKXEN SIM 1", lens: Lens | None = None,
                 freed_target: tuple[str, int] | None = None, fps: float = 30.0, video_delay: float = 0.08,
                 command_delay: float = 0.03, size: tuple[int, int] = (1920, 1080), camera_args: dict | None = None):
        self.scene, self.ndi_name, self.lens = scene, ndi_name, lens or Lens()
        self.fps, self.video_delay, self.size = fps, video_delay, size
        self.camera = FakeCamera(freed_target=freed_target, command_delay=command_delay, **(camera_args or {}))
        self.camera.state.tilt = -3.0
        self.camera.state.zoom = self.lens.zoom_for_hfov(40.0)
        self.frames_sent = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self, host: str = "127.0.0.1", visca_port: int = 52381) -> int:
        port = await self.camera.start(host=host, port=visca_port)
        self._thread = threading.Thread(target=self._send_video, name="sim-video", daemon=True)
        self._thread.start()
        return port

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.camera.stop()

    def _send_video(self) -> None:
        from fractions import Fraction

        from cyndilib.sender import Sender
        from cyndilib.video_frame import VideoSendFrame
        from cyndilib.wrapper.ndi_structs import FourCC

        sender = Sender(self.ndi_name)
        vf = VideoSendFrame()
        vf.set_resolution(*self.size)
        vf.set_frame_rate(Fraction(round(self.fps * 1000), 1000))
        vf.set_fourcc(FourCC.BGRX)
        sender.set_video_frame(vf)
        sender.open()
        start = time.monotonic()
        try:
            while not self._stop.is_set():
                t = time.monotonic() - self.video_delay
                pan, tilt, zoom = self.camera.pose_at(t)
                frame = self.scene.render(pan, tilt, self.lens.hfov(zoom), t, self.size)
                sender.write_video(cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA).ravel())
                self.frames_sent += 1
                next_time = start + self.frames_sent / self.fps
                time.sleep(max(0.0, next_time - time.monotonic()))
        finally:
            sender.close()


async def run_forever(camera: VirtualCamera, host: str, visca_port: int) -> None:
    await camera.start(host, visca_port)
    try:
        await asyncio.Event().wait()
    finally:
        camera.stop()

"""A real video as the stage: the virtual camera pans and zooms over a clip of real people (e.g.
stock footage of a speaker), like a camera aimed at that room.

The clip's picture is the camera's whole world: it covers `hfov` degrees across, its centre is
pan 0 / tilt 0. The camera's view at (pan, tilt, field of view) is cut out of the current clip
frame and scaled to the output size, so tracking runs on real people, real walking and real
light. Panning blurs the picture a little, like a real camera's shutter. The clip plays in real
time and starts over at the end (the people then jump back to where they began). Looking past
the clip's edge shows black.
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

SHUTTER_S = 1 / 50  # exposure time for the motion blur while panning


class FootageScene:
    def __init__(self, path: str, hfov: float = 55.0, blur: bool = True):
        import av

        self.path, self.hfov, self.blur = path, hfov, blur
        with av.open(path) as container:
            stream = container.streams.video[0]
            self.width, self.height = stream.width, stream.height
            self.fps = float(stream.average_rate or 25)
        self.ppd = self.width / hfov  # clip pixels per degree
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last: tuple[float, float] | None = None  # (t, pan) of the previous render, for the blur
        self.loops = 0
        threading.Thread(target=self._play, name="footage", daemon=True).start()
        deadline = time.monotonic() + 10
        while self._frame is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._frame is None:
            raise RuntimeError(f"no picture from {path}")

    def stop(self) -> None:
        self._stop.set()

    def _play(self) -> None:
        """Decode the clip in real time, again and again; keep the current frame."""
        import av

        while not self._stop.is_set():
            with av.open(self.path) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                start = time.monotonic()
                for i, frame in enumerate(container.decode(stream)):
                    if self._stop.is_set():
                        return
                    image = frame.to_ndarray(format="bgr24")
                    wait = start + i / self.fps - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    with self._lock:
                        self._frame = image
            self.loops += 1

    def render(self, pan: float, tilt: float, hfov: float, t: float, size: tuple[int, int]) -> np.ndarray:
        """The camera's picture: the part of the current clip frame it looks at, scaled to `size`."""
        with self._lock:
            frame = self._frame
        out_w, out_h = size
        crop_w = max(16.0, hfov * self.ppd)
        crop_h = crop_w * out_h / out_w
        cx, cy = self.width / 2 + pan * self.ppd, self.height / 2 - tilt * self.ppd
        x1, y1 = round(cx - crop_w / 2), round(cy - crop_h / 2)
        x2, y2 = x1 + round(crop_w), y1 + round(crop_h)
        if x1 >= 0 and y1 >= 0 and x2 <= self.width and y2 <= self.height:
            crop = frame[y1:y2, x1:x2]
        else:  # looking past the clip's edge: black there
            crop = np.zeros((y2 - y1, x2 - x1, 3), np.uint8)
            sx1, sy1, sx2, sy2 = max(x1, 0), max(y1, 0), min(x2, self.width), min(y2, self.height)
            if sx2 > sx1 and sy2 > sy1:
                crop[sy1 - y1:sy2 - y1, sx1 - x1:sx2 - x1] = frame[sy1:sy2, sx1:sx2]
        out = cv2.resize(crop, size, interpolation=cv2.INTER_LINEAR)
        if self.blur:
            out = self._motion_blur(out, pan, hfov, t, out_w)
        return out

    def _motion_blur(self, image: np.ndarray, pan: float, hfov: float, t: float, out_w: int) -> np.ndarray:
        last, self._last = self._last, (t, pan)
        if not last or t <= last[0]:
            return image
        speed = (pan - last[1]) / (t - last[0])  # degrees per second
        length = int(abs(speed) * SHUTTER_S * out_w / hfov)  # pixels the picture moves while exposed
        if length < 2:
            return image
        kernel = np.full((1, min(length, 60)), 1.0 / min(length, 60), np.float32)
        return cv2.filter2D(image, -1, kernel)

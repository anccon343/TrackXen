"""Where camera video comes from: NDI (the normal case), an HDMI/SDI capture device, or a video
file (replay tests).

Both keep only the newest frame and record real arrival times, so statistics and the
tracker see the same timing the live system would.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2

from trackxen.devices.ndi_in import Frame, NdiReceiver


@dataclass
class SourceConfig:
    camera: str  # camera id, e.g. "cam1"
    kind: str = "ndi"  # "ndi", "capture" or "file"
    name: str = ""  # NDI source name (full or part of it), capture device number, or file path
    low_bandwidth: bool = False
    loop: bool = True  # files: start over at the end


class FileSource:
    """Plays a video file in real time as if it were a live camera."""

    def __init__(self, path: str, loop: bool = True):
        self.path, self.loop = path, loop
        self.source_name = path
        self.frames_received = 0
        self.arrivals: deque[float] = deque(maxlen=32)
        self.ndi_frames_total = self.ndi_frames_dropped = 0
        self.connected = False
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 5.0) -> None:
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise LookupError(f"can't open video file: {self.path}")
        cap.release()
        self._thread = threading.Thread(target=self._run, name=f"file:{self.path}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        cap = cv2.VideoCapture(self.path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.connected = True
        start, shown = time.monotonic(), 0
        while not self._stop.is_set():
            ok, image = cap.read()
            if not ok:
                if not self.loop:
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            shown += 1
            time.sleep(max(0.0, start + shown / fps - time.monotonic()))
            arrived = time.monotonic()
            self.frames_received += 1
            self.ndi_frames_total += 1
            self.arrivals.append(arrived)
            with self._lock:
                self._latest = Frame(image, arrived, 0.0, self.frames_received)
        cap.release()
        self.connected = False


class CaptureSource(FileSource):
    """An HDMI/SDI capture card or USB capture device (Windows DirectShow), for a camera whose video
    doesn't come over NDI. Control still goes over IP (VISCA). Asks for 1080p at 30 fps and takes
    what the device gives. Most capture devices allow one program at a time: if vMix uses the card
    on this PC, take the camera from vMix as NDI instead."""

    def __init__(self, index: int, width: int = 1920, height: int = 1080, fps: float = 30.0):
        super().__init__(f"capture device {index}")
        self.index, self.size, self.fps = index, (width, height), fps

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def start(self, timeout: float = 5.0) -> None:
        cap = self._open()
        if not cap.isOpened():
            raise LookupError(f"capture device {self.index} not found or in use by another program")
        cap.release()
        self._thread = threading.Thread(target=self._run, name=f"capture:{self.index}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():  # (re)open: the device may be unplugged and plugged in again
            cap = self._open()
            failures = 0
            while cap.isOpened() and not self._stop.is_set() and failures < 50:
                ok, image = cap.read()  # waits for the device's next frame
                if not ok:
                    failures += 1
                    time.sleep(0.02)
                    continue
                failures = 0
                arrived = time.monotonic()
                self.connected = True
                self.frames_received += 1
                self.ndi_frames_total += 1
                self.arrivals.append(arrived)
                with self._lock:
                    self._latest = Frame(image, arrived, 0.0, self.frames_received)
            cap.release()
            self.connected = False
            self._stop.wait(1.0)


def capture_devices(count: int = 6) -> list[dict]:
    """Capture devices that open, by DirectShow number, with the picture size they give.
    (Opening a device can switch on a webcam's light for a moment.)"""
    found = []
    for index in range(count):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            found.append({"index": index, "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                          "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))})
        cap.release()
    return found


def open_source(cfg: SourceConfig) -> NdiReceiver | FileSource:
    if cfg.kind == "file":
        return FileSource(cfg.name, cfg.loop)
    if cfg.kind == "capture":
        return CaptureSource(int(cfg.name or 0))
    return NdiReceiver(cfg.name, low_bandwidth=cfg.low_bandwidth)

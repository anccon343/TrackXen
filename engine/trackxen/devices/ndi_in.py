"""NDI video input through cyndilib (bundles the NDI 6 runtime).

`find_sources()` lists NDI sources on the network. `NdiReceiver` keeps the newest
frame from one source as a BGR numpy array, with the time it arrived, so the
tracker never works on old, queued frames.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.video_frame import VideoFrameSync
from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat


@dataclass(frozen=True)
class NdiSourceInfo:
    name: str  # e.g. "BIRDDOG-12345 (CAM)"
    host_name: str  # e.g. "BIRDDOG-12345"
    stream_name: str


def find_sources(timeout: float = 3.0) -> list[NdiSourceInfo]:
    finder = Finder()
    finder.open()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            finder.wait_for_sources(max(0.0, deadline - time.monotonic()))
            finder.update_sources()
        return [NdiSourceInfo(s.name, s.host_name, s.stream_name) for s in finder.iter_sources()]
    finally:
        finder.close()


@dataclass
class Frame:
    image: np.ndarray  # BGR, uint8, shape (height, width, 3)
    received: float  # time.monotonic() when it arrived
    timestamp: float  # sender's timestamp (POSIX seconds), 0 if unknown
    number: int


class NdiReceiver:
    """Receives one NDI source on a background thread, keeping only the latest frame."""

    def __init__(self, source_name: str, low_bandwidth: bool = False, poll_hz: float = 250.0):
        self.source_name, self.poll_hz = source_name, poll_hz
        self.bandwidth = RecvBandwidth.lowest if low_bandwidth else RecvBandwidth.highest
        self._finder = Finder()
        self._receiver: Receiver | None = None
        self._video = VideoFrameSync()
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames_received = 0
        self.arrivals: deque[float] = deque(maxlen=32)  # monotonic arrival time of recent frames
        # NDI SDK receive statistics (NDIlib_recv_get_performance), refreshed once a second
        self.ndi_frames_total = 0
        self.ndi_frames_dropped = 0
        self.connected = False

    def _find(self) -> object | None:
        names = self._finder.get_source_names()
        if self.source_name in names:
            return self._finder.get_source(self.source_name)
        wanted = self.source_name.lower()
        for name in names:  # "TRACKXEN SIM 1" also finds "ANTTONPC (TRACKXEN SIM 1)"
            if wanted in name.lower():
                self.source_name = name
                return self._finder.get_source(name)
        return None

    def start(self, timeout: float = 5.0) -> None:
        self._finder.open()
        deadline = time.monotonic() + timeout
        source = None
        while time.monotonic() < deadline and source is None:
            self._finder.wait_for_sources(0.5)
            self._finder.update_sources()
            source = self._find()
        if source is None:
            self._finder.close()
            raise LookupError(f"NDI source not found: {self.source_name}")
        self._receiver = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=self.bandwidth,
                                  recv_name="trackxen")
        self._receiver.frame_sync.set_video_frame(self._video)
        self._receiver.set_source(source)
        self._thread = threading.Thread(target=self._run, name=f"ndi:{self.source_name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._receiver:
            self._receiver.disconnect()
        self._finder.close()

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    @property
    def receiver(self) -> Receiver | None:
        """The underlying cyndilib receiver (its `.ptz` is NDI PTZ control)."""
        return self._receiver

    def _run(self) -> None:
        last_ts = None
        interval = 1.0 / self.poll_hz
        next_perf = 0.0
        while not self._stop.is_set():
            assert self._receiver is not None
            self._receiver.frame_sync.capture_video()
            width, height = self._video.get_resolution()
            ts = self._video.get_timestamp_posix()
            if width and height and ts != last_ts:
                arrived = time.monotonic()
                last_ts = ts
                stride = self._video.get_line_stride() or width * 4
                raw = np.asarray(self._video.get_array(), dtype=np.uint8)
                if raw.size >= stride * height:
                    bgrx = raw[: stride * height].reshape(height, stride // 4, 4)[:, :width]
                    image = np.ascontiguousarray(bgrx[:, :, :3])
                    self.frames_received += 1
                    self.arrivals.append(arrived)
                    with self._lock:
                        self._latest = Frame(image, arrived, ts, self.frames_received)
            now = time.monotonic()
            if now >= next_perf:
                next_perf = now + 1.0
                video = self._receiver.get_performance_data()["video"]
                self.ndi_frames_total = int(video["frames_total"])
                self.ndi_frames_dropped = int(video["frames_dropped"])
                self.connected = bool(self._receiver.is_connected())
            time.sleep(interval)

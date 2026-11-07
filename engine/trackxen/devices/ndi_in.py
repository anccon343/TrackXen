"""NDI video input through cyndilib (bundles the NDI 6 runtime).

`find_sources()` lists NDI sources on the network. `NdiReceiver` keeps the newest
frame from one source as a BGR numpy array, with the time it arrived, so the
tracker never works on old, queued frames.

The finder is never asked to wait: cyndilib's `wait_for_sources()` blocks inside the NDI
library while holding the GIL, which paused every other thread of the process for the whole
wait (measured: 3.0 s for a 3 s search, so the camera control loop and watchdog froze while
a camera moved). The finder discovers sources on its own threads; we read its list and sleep.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np
from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.video_frame import VideoFrameSync
from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat

POLL_INTERVAL = 0.05  # seconds between looks at the finder's source list


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
        while (left := deadline - time.monotonic()) > 0:
            time.sleep(min(POLL_INTERVAL, left))  # (lets the other threads run, unlike wait_for_sources)
            finder.update_sources()
        return [NdiSourceInfo(s.name, s.host_name, s.stream_name) for s in finder.iter_sources() if s.valid]
    finally:
        finder.close()


def _name_parts(name: str) -> tuple[str, str]:
    """Host and stream name of "HOST (STREAM)", lower case."""
    host, _, stream = name.lower().partition(" (")
    return host, stream.removesuffix(")")


def _apart(part: str, name: str) -> bool:
    """Is `part` in `name` without splitting a word of it? "birddog" in "birddog-1 (cam)" is; "cam1" in
    "pc (cam10)" (camera 10's source) is not."""
    start = name.find(part)
    while start >= 0:
        before, after = name[start - 1:start], name[start + len(part):start + len(part) + 1]
        if not (before.isalnum() and part[0].isalnum()) and not (after.isalnum() and part[-1].isalnum()):
            return True
        start = name.find(part, start + 1)
    return False


def to_bgr(bgrx: np.ndarray) -> np.ndarray:
    """A new BGR copy of an NDI BGRX picture. OpenCV's conversion takes 1 ms on a 1080p frame;
    numpy's copy of the first three channels took 7 ms (measured), and every frame waited for it."""
    try:
        return cv2.cvtColor(bgrx, cv2.COLOR_BGRA2BGR)
    except cv2.error:  # a row layout OpenCV can't take as it is
        return np.ascontiguousarray(bgrx[:, :, :3])


@dataclass
class Frame:
    image: np.ndarray  # BGR, uint8, shape (height, width, 3)
    received: float  # time.monotonic() when it arrived (shared time base with the core)
    timestamp: float  # sender's timestamp (POSIX seconds), 0 if unknown
    number: int
    # time.perf_counter() when it arrived, for exact durations: on Windows time.monotonic() only
    # ticks every 15.6 ms, so frame -> result times measured with it came out as 0, 16 or 31 ms
    measured: float = 0.0


class NdiReceiver:
    """Receives one NDI source on a background thread, keeping only the latest frame."""

    def __init__(self, source_name: str, low_bandwidth: bool = False, poll_hz: float = 250.0):
        self.source_name, self.poll_hz = source_name, poll_hz
        self.bandwidth = RecvBandwidth.lowest if low_bandwidth else RecvBandwidth.highest
        self._finder = Finder()
        self._search_until = 0.0  # start() waits for the source until then (set when the finder opens)
        self._matches: list[str] = []  # sources the last look found for a partial name
        self._receiver: Receiver | None = None
        self._video = VideoFrameSync()
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames_received = 0
        self.arrivals: deque[float] = deque(maxlen=32)  # perf_counter arrival time of recent frames
        # NDI SDK receive statistics (NDIlib_recv_get_performance), refreshed once a second
        self.ndi_frames_total = 0
        self.ndi_frames_dropped = 0
        self.connected = False

    def _find(self) -> object | None:
        """The configured source by its exact name, or by part of a name ("TRACKXEN SIM 1" also finds
        "ANTTONPC (TRACKXEN SIM 1)") when only one source has that part. A full "HOST (STREAM)" name is
        taken only as it is, so "ALTAR (CAM)" never picks "SIDEALTAR (CAM)" before its own source shows up.
        A part that is a whole host or stream name wins; a part inside a longer one ("BIRDDOG" in
        "BIRDDOG-1 (CAM)") is taken only once the first search is over, so a source not announced yet gets
        its chance, and never when it splits a word: "CAM1" never takes "PC (CAM10)", however long camera 1
        takes to boot."""
        names, self._matches = self._finder.get_source_names(), []
        if self.source_name in names:
            return self._finder.get_source(self.source_name)
        wanted = self.source_name.lower()
        if " (" in wanted and wanted.endswith(")"):
            matches = [name for name in names if name.lower() == wanted]
        else:
            matches = [name for name in names if wanted in name.lower()]
            whole = [name for name in matches if wanted in _name_parts(name)]
            if whole or time.monotonic() < self._search_until:  # "CAM1": "PC (CAM1)", not "PC (CAM10)"
                matches = whole
            else:
                matches = [name for name in matches if _apart(wanted, name.lower())]
        self._matches = matches
        if len(matches) != 1:  # none yet, or several: don't guess which camera's video to track
            return None
        self.source_name = matches[0]
        return self._finder.get_source(matches[0])

    def start(self, timeout: float = 5.0) -> None:
        """Connect to the source, waiting until `timeout` seconds after the finder opened for it to be
        announced. After a miss the finder stays open and keeps looking, so a retry (the worker retries
        every few seconds on its detection thread) only checks what it found meanwhile and returns at once."""
        if not self._finder.is_open:
            self._finder.open()
            self._search_until = time.monotonic() + timeout
        while True:
            self._finder.update_sources()
            searched = time.monotonic() >= self._search_until  # (before _find: it then takes a part of a name too)
            source = self._find()
            if source is not None or searched:
                break
            time.sleep(POLL_INTERVAL)  # (lets the other threads run, unlike wait_for_sources)
        if source is None:
            if len(self._matches) > 1:
                raise LookupError(f"NDI name {self.source_name} matches several sources: {', '.join(self._matches)}"
                                  " (choose one of them)")
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
                arrived, exact = time.monotonic(), time.perf_counter()
                last_ts = ts
                stride = self._video.get_line_stride() or width * 4
                raw = np.asarray(self._video.get_array(), dtype=np.uint8)
                if raw.size >= stride * height:
                    bgrx = raw[: stride * height].reshape(height, stride // 4, 4)[:, :width]
                    image = to_bgr(bgrx)
                    self.frames_received += 1
                    self.arrivals.append(exact)
                    with self._lock:
                        self._latest = Frame(image, arrived, ts, self.frames_received, exact)
            now = time.monotonic()
            if now >= next_perf:
                next_perf = now + 1.0
                video = self._receiver.get_performance_data()["video"]
                self.ndi_frames_total = int(video["frames_total"])
                self.ndi_frames_dropped = int(video["frames_dropped"])
                self.connected = bool(self._receiver.is_connected())
            time.sleep(interval)

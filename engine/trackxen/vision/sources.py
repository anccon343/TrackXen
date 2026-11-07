"""Where camera video comes from: NDI (the normal case), a camera's own network stream (RTSP,
e.g. PTZOptics), an HDMI/SDI capture device, or a video file (replay tests).

All keep only the newest frame and record real arrival times, so statistics and the
tracker see the same timing the live system would.
"""

from __future__ import annotations

import re
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2

from trackxen.devices.ndi_in import Frame, NdiReceiver


@dataclass
class SourceConfig:
    camera: str  # camera id, e.g. "cam1"
    kind: str = "ndi"  # "ndi", "stream", "capture" or "file"
    name: str = ""  # NDI source name (full or part of it), stream URL, capture device number, or file path
    low_bandwidth: bool = False
    loop: bool = True  # files: start over at the end
    transport: str = "tcp"  # streams: how RTSP packets travel ("tcp" or "udp")


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
            arrived, exact = time.monotonic(), time.perf_counter()
            self.frames_received += 1
            self.ndi_frames_total += 1
            self.arrivals.append(exact)
            with self._lock:
                self._latest = Frame(image, arrived, 0.0, self.frames_received, exact)
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
                arrived, exact = time.monotonic(), time.perf_counter()
                self.connected = True
                self.frames_received += 1
                self.ndi_frames_total += 1
                self.arrivals.append(exact)
                with self._lock:
                    self._latest = Frame(image, arrived, 0.0, self.frames_received, exact)
            cap.release()
            self.connected = False
            self._stop.wait(1.0)


class StreamSource:
    """A camera's own network stream (RTSP; PTZOptics: rtsp://IP:554/1 main, /2 sub stream), decoded
    with FFmpeg through PyAV for the least delay:
    - no input buffering (`fflags=nobuffer`, `max_delay=0`) and a low-delay decoder;
    - slice threads only (frame threads would hold frames back);
    - the newest frame kept, as with NDI.
    Reconnects when the stream stops. Measured: frames, arrival gaps, decode time per frame, and
    damaged or undecodable frames (counted as dropped, like NDI's)."""

    OPTIONS = {"fflags": "nobuffer", "max_delay": "0", "probesize": "500000", "analyzeduration": "500000"}

    def __init__(self, url: str, transport: str = "tcp", loop: bool = False, timeout: float = 5.0):
        self.url, self.transport, self.loop, self.timeout = url, transport, loop, timeout
        self.source_name = re.sub(r"//[^/@]+@", "//", url)  # never show a password from the URL
        self.frames_received = 0
        self.arrivals: deque[float] = deque(maxlen=32)
        self.ndi_frames_total = self.ndi_frames_dropped = 0  # frames the stream brought / that were damaged
        self.decode_times: deque[float] = deque(maxlen=64)  # ms per frame (decoding and converting to BGR)
        self.connected = False
        self.last_error = ""
        self.size = (0, 0)
        self.codec = ""
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def decode_ms(self) -> float | None:
        return round(statistics.median(self.decode_times), 2) if self.decode_times else None

    def start(self, timeout: float = 5.0) -> None:
        """Starts the reader; connecting happens there (and again whenever the stream stops), so a
        camera that is still booting doesn't hold anything up."""
        self._thread = threading.Thread(target=self._run, name=f"stream:{self.source_name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.timeout + 1.0)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        import av

        options = {**self.OPTIONS}
        if self.url.lower().startswith("rtsp"):
            options["rtsp_transport"] = self.transport
        while not self._stop.is_set():
            try:
                container = av.open(self.url, options=options, timeout=(self.timeout, self.timeout))
            except Exception as e:  # noqa: BLE001 - reported (stats), tried again
                self.last_error = f"can't open {self.source_name}: {self._clean(e)}"
                self._stop.wait(2.0)
                continue
            failed = False
            try:
                self._read(container)
            except Exception as e:  # noqa: BLE001 - e.g. no video in the stream: reported, tried again
                self.last_error, failed = f"{self.source_name}: {self._clean(e)}", True
            finally:
                self.connected = False
                container.close()
            if failed or not self.loop:
                self._stop.wait(1.0)  # the camera rebooted or the network dropped: open it again

    def _clean(self, error: Exception) -> str:
        """An error's text without the password: FFmpeg's messages repeat the URL as it was given."""
        return re.sub(r"//[^/@\s]+@", "//", str(error).replace(self.url, self.source_name))

    def _read(self, container) -> None:
        import av

        if not container.streams.video:
            raise LookupError("no video in this stream")
        stream = container.streams.video[0]
        stream.thread_type = "SLICE"
        stream.codec_context.flags |= av.codec.context.Flags.low_delay
        self.codec = stream.codec_context.name
        for packet in container.demux(stream):
            if self._stop.is_set():
                return
            if packet.size == 0:
                continue
            self.ndi_frames_total += 1
            t0 = time.perf_counter()
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                self.ndi_frames_dropped += 1
                continue
            if packet.is_corrupt:
                self.ndi_frames_dropped += 1
            for frame in frames:
                image = frame.to_ndarray(format="bgr24")
                arrived, exact = time.monotonic(), time.perf_counter()
                self.decode_times.append((exact - t0) * 1000)
                self.frames_received += 1
                self.arrivals.append(exact)
                self.size = (image.shape[1], image.shape[0])
                self.connected, self.last_error = True, ""
                with self._lock:
                    self._latest = Frame(image, arrived, 0.0, self.frames_received, exact)


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


def open_source(cfg: SourceConfig) -> NdiReceiver | FileSource | StreamSource:
    if cfg.kind == "file":
        return FileSource(cfg.name, cfg.loop)
    if cfg.kind == "capture":
        return CaptureSource(int(cfg.name or 0))
    if cfg.kind == "stream":
        return StreamSource(cfg.name, cfg.transport, loop=cfg.loop and not cfg.name.lower().startswith("rtsp"))
    return NdiReceiver(cfg.name, low_bandwidth=cfg.low_bandwidth)

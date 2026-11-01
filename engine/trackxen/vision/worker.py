"""Vision worker: runs in its own process. Video in -> people -> tracks -> results to the core.

If this process stalls or crashes, the core's watchdog stops the cameras and restarts it.
Every number it reports is measured here: frame arrival gaps, NDI's own dropped frames,
frames skipped because the detector was busy, and the time of each detection stage.

Messages to the core (dicts on the results queue):
  ready   {engine, pid}
  tracks  {camera, frame, t_frame, t_done, width, height, tracks, skipped, timing, engine}
  preview {camera, jpeg, t_frame, width, height}
  stats   {camera, connected, frames_received, ndi_total, ndi_dropped, gaps_ms, source}
          (+ decode_ms, codec, size for a network stream)
  engine  {engine, message}  (e.g. TensorRT finished building and took over)
  error   {message, camera?}
  face_guide    {camera, ok, problem, box}   (registration frame, about 5 times a second)
  face_captured {camera, capture, feature, jpeg}
Commands from the core: {"type": "stop"}, {"type": "preview", "camera": id, "enabled": bool},
{"type": "max_detect_fps", "value": float}, {"type": "tracker", "settings": {PersonTracker fields}},
{"type": "faces", "gallery": [{id, feature}]}, {"type": "face_guide", "camera": id, "on": bool},
{"type": "face_capture", "camera": id, "capture": int}
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2

from trackxen.vision import faces
from trackxen.vision.pipeline import GpuStage, Job
from trackxen.vision.sources import SourceConfig, StreamSource, open_source
from trackxen.vision.tracker import PersonTracker


@dataclass
class WorkerConfig:
    sources: list[SourceConfig]
    model_dir: str
    model_name: str = "yolo26s-pose-640"  # files <name>-b1.onnx and <name>-b4.onnx
    engines: tuple[str, ...] = ("tensorrt", "cuda", "directml", "cpu")
    conf: float = 0.1  # keep weak detections: the tracker uses them to hold on to people
    preview_fps: float = 12.0
    preview_width: int = 960
    jpeg_quality: int = 70
    max_detect_fps: float = 0.0  # 0 = every new frame
    pipeline: bool = True  # GPU and CPU work at the same time (vision/pipeline.py); off: one after the other
    cache_dir: str = ""  # TensorRT engine cache (default: <model_dir>/trt-cache)
    gpu_libs: str = ""  # NVIDIA DLLs downloaded on first start (core/gpu_libs.py); "" = the Python packages'
    previews: dict[str, bool] = field(default_factory=dict)  # camera -> preview on (default on)
    tracker: dict = field(default_factory=dict)  # PersonTracker settings (Advanced mode)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["engines"] = list(self.engines)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WorkerConfig:
        d = dict(d)
        d["sources"] = [SourceConfig(**s) for s in d["sources"]]
        d["engines"] = tuple(d["engines"])
        return cls(**d)


class Detectors:
    """The detectors in use, plus TensorRT being built in the background if it isn't cached yet.

    With several cameras there are two: a batch-of-4 model for when several frames are waiting,
    and a single-frame model for when only one is (frames from unsynchronised cameras mostly
    arrive one at a time; padding them to 4 blank-filled images wastes most of the GPU's work).
    """

    def __init__(self, cfg: WorkerConfig, batch: int, emit):
        from trackxen.vision.runtime import available_engines
        from trackxen.vision.yolo_pose import PoseDetector

        def make(model: Path, engines: tuple[str, ...]):
            cache = Path(cfg.cache_dir) if cfg.cache_dir else Path(cfg.model_dir) / "trt-cache"
            return PoseDetector(model, cache_dir=cache, engines=engines, conf=cfg.conf)

        sizes = sorted({1, batch})
        self.models = {n: Path(cfg.model_dir) / f"{cfg.model_name}-b{n}.onnx" for n in sizes}
        for model in self.models.values():
            if not model.exists():
                raise FileNotFoundError(f"model missing: {model}")
        usable = [e for e in cfg.engines if e in available_engines()]
        fast = [e for e in usable if e != "tensorrt"] or usable
        self.current = {n: make(model, tuple(fast)) for n, model in self.models.items()}
        self._next: dict | None = None
        self.building = "tensorrt" in usable and any(d.engine != "tensorrt" for d in self.current.values())
        if self.building:  # (first start on this GPU, or a new model: takes minutes)
            def build():
                try:
                    self._next = {n: make(model, ("tensorrt",)) for n, model in self.models.items()}
                    emit({"type": "engine", "engine": "tensorrt", "message": "TensorRT engine ready"})
                except Exception as e:  # noqa: BLE001 - stay on the current engine
                    emit({"type": "engine", "engine": self.engine,
                          "message": f"TensorRT unavailable: {str(e).splitlines()[0][:200]}"})
                finally:
                    self.building = False
            threading.Thread(target=build, name="tensorrt-build", daemon=True).start()

    @property
    def engine(self) -> str:
        return next(iter(self.current.values())).engine

    def get(self, frames: int = 1):
        """The detector for this many frames (the single-frame one for one frame)."""
        if self._next is not None:
            self.current, self._next = self._next, None
        return self.current[1] if frames == 1 else self.current[max(self.current)]


class VisionLoop:
    """The worker's main loop, usable in-process (tests) or in its own process (run_worker)."""

    def __init__(self, cfg: WorkerConfig, emit):
        self.cfg, self.emit = cfg, emit
        self.sources = {s.camera: open_source(s) for s in cfg.sources}
        self.trackers = {cam: PersonTracker(**cfg.tracker) for cam in self.sources}
        self.last_number = {cam: 0 for cam in self.sources}
        self.last_preview = {cam: 0.0 for cam in self.sources}
        self.previews = {cam: cfg.previews.get(cam, True) for cam in self.sources}
        self.max_detect_fps = cfg.max_detect_fps
        self._last_detect: dict[str, float] = {}
        self._tokens: dict[str, float] = {}
        self._passed: dict[str, int] = {}  # frame left out by the rate limit (don't analyse it later)
        self._allowance: set[str] = set()  # cameras the rate limit let through, frame not taken yet
        self._stream_errors: dict[str, str] = {}  # last error reported per network stream
        self.faces: faces.FaceWorker | None = None  # started when faces are registered or being registered
        # preview JPEGs are made on their own threads: with 4 cameras that's ~48 a second, too much
        # work for the detection loop (OpenCV releases the GIL, so this runs in parallel)
        self._encoder = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview")
        self._encoding: set[str] = set()
        self._started: set[str] = set()
        self._next_start_try = 0.0
        batch = 1 if len(self.sources) <= 1 else 4
        self.detectors = Detectors(cfg, batch, emit)
        self.batch = batch
        self.gpu = GpuStage() if cfg.pipeline else None

    def start_sources(self) -> None:
        """(Re)try opening sources that aren't running yet (a camera may come online later)."""
        now = time.monotonic()
        if now < self._next_start_try:
            return
        self._next_start_try = now + 5.0
        for cam, src in self.sources.items():
            if cam in self._started:
                continue
            try:
                src.start(timeout=2.0)
                self._started.add(cam)
            except Exception as e:  # noqa: BLE001 - report and retry later
                self.emit({"type": "error", "camera": cam, "message": str(e)})

    def command(self, msg: dict) -> bool:
        """Apply a command; returns False when the worker should stop."""
        kind = msg.get("type")
        if kind == "stop":
            return False
        if kind == "preview":
            self.previews[msg["camera"]] = bool(msg["enabled"])
        elif kind == "max_detect_fps":
            self.max_detect_fps = float(msg["value"])
        elif kind == "tracker":  # Advanced mode thresholds, applied live
            for tracker in self.trackers.values():
                for key, value in msg["settings"].items():
                    if hasattr(tracker, key):
                        setattr(tracker, key, value)
        elif kind in ("faces", "face_guide", "face_capture"):
            self._face_command(msg)
        return True

    def _face_command(self, msg: dict) -> None:
        if self.faces is None:
            models = Path(self.cfg.model_dir)
            if not faces.available(models):
                self.emit({"type": "error", "message": "face models missing: run tools/get_face_models.py"})
                return
            self.faces = faces.FaceWorker(models, self.emit)
        kind = msg["type"]
        if kind == "faces":
            self.faces.set_gallery(msg["gallery"])
        elif kind == "face_guide":
            if msg["on"]:
                self.faces.guides.setdefault(msg["camera"], 0)
            else:
                self.faces.guides.pop(msg["camera"], None)
        else:
            self.faces.guides[msg["camera"]] = int(msg["capture"])

    def _allowed(self, cam: str, now: float) -> bool:
        """Per-camera token bucket for max_detect_fps: the average rate stays at the limit, but a
        frame is analysed the moment it arrives or not at all. (Waiting for the next time slot
        instead added up to a frame of delay, and with jittery arrivals skipped a third of a
        30 fps source at a 30/s limit.)"""
        if self.max_detect_fps <= 0:
            return True
        last = self._last_detect.get(cam)
        tokens = self._tokens.get(cam, 1.5)
        if last is not None:
            tokens = min(1.5, tokens + (now - last) * self.max_detect_fps)
        self._last_detect[cam] = now
        if tokens < 0.8:  # a little early is fine: the average still holds
            self._tokens[cam] = tokens
            return False
        self._tokens[cam] = tokens - 1.0
        return True

    def step(self) -> bool:
        """Detect on the newest frames once; returns False if there was nothing to do."""
        self.start_sources()
        worked = False
        if self.gpu:
            for job in self.gpu.collect():
                self._finish(job)
                worked = True
            if not self.gpu.has_room():  # a batch already waits for the GPU: wait for a result, don't spin
                for job in self.gpu.collect(wait=0.005):
                    self._finish(job)
                return True
        now = time.monotonic()
        fresh = self._fresh(now)
        if not fresh:
            return worked
        if self.gpu and self.gpu.pending and len(fresh) < min(self._live(now), self.batch):
            return worked  # the GPU is busy anyway: let the other cameras' frames fill this batch
        for i in range(0, len(fresh), self.batch):
            chunk = fresh[i:i + self.batch]
            detector = self.detectors.get(len(chunk))
            batch = detector.prepare([frame.image for _, frame in chunk])
            job = Job(detector, batch, [(cam, frame, self._take(cam, frame)) for cam, frame in chunk])
            if self.gpu:
                self.gpu.submit(job)
            else:
                t0 = time.perf_counter()
                job.out = detector.infer(batch)
                job.infer_ms = (time.perf_counter() - t0) * 1000
                self._finish(job)
        return True

    def _fresh(self, now: float) -> list:
        """(camera, frame) for every camera with a frame that wasn't taken yet."""
        fresh = []
        for cam in self._started:
            frame = self.sources[cam].latest()
            if not frame or frame.number in (self.last_number[cam], self._passed.get(cam)):
                continue
            if cam not in self._allowance:  # the rate limit decides once per detection, not per look
                if not self._allowed(cam, now):
                    self._passed[cam] = frame.number  # skipped for the frame rate limit (counted in "skipped")
                    continue
                self._allowance.add(cam)
            fresh.append((cam, frame))
        return fresh

    def _live(self, now: float) -> int:
        """Cameras whose video is coming in (a camera without a picture mustn't hold up a batch)."""
        return sum(1 for cam in self._started
                   if (frame := self.sources[cam].latest()) and now - frame.received < 0.25)

    def _take(self, cam: str, frame) -> int:
        """`frame` goes to detection; returns how many frames were skipped since the last one."""
        self._allowance.discard(cam)
        skipped = max(0, frame.number - self.last_number[cam] - 1)
        self.last_number[cam] = frame.number
        return skipped

    def _finish(self, job: Job) -> None:
        """Decode a batch the GPU ran, then track, report and preview each of its frames."""
        if job.error:
            raise job.error  # the worker stops and the core restarts it, as with any failure here
        t0 = time.perf_counter()
        people = job.detector.decode(job.batch, job.out)
        timing = {"pre_ms": round(job.batch.pre_ms, 3), "infer_ms": round(job.infer_ms, 3),
                  "post_ms": round((time.perf_counter() - t0) * 1000, 3)}
        for (cam, frame, skipped), found in zip(job.payload, people):
            tracks = self.trackers[cam].update(found, frame.received)
            h, w = frame.image.shape[:2]
            if self.faces:
                self.faces.submit(cam, frame.image, tracks, time.monotonic())
            self.emit({"type": "tracks", "camera": cam, "frame": frame.number, "t_frame": frame.received,
                       "t_done": time.monotonic(), "width": w, "height": h,
                       "pipeline_ms": round((time.perf_counter() - frame.measured) * 1000, 2) if frame.measured else None,
                       "tracks": [self._track_dict(cam, t) for t in tracks], "skipped": skipped,
                       "timing": timing, "engine": job.detector.engine, "batch": len(job.payload)})
            self._maybe_preview(cam, frame)

    def _track_dict(self, cam: str, track) -> dict:
        d = track.to_dict()
        face = self.faces.identity(cam, track.id) if self.faces else None
        if face:
            d["face"] = face
        return d

    def _maybe_preview(self, cam: str, frame) -> None:
        if not self.previews.get(cam, True) or cam in self._encoding:
            return
        now = time.monotonic()
        if now - self.last_preview[cam] < 1.0 / max(self.cfg.preview_fps, 0.1):
            return
        self.last_preview[cam] = now
        self._encoding.add(cam)
        self._encoder.submit(self._encode, cam, frame)

    def _encode(self, cam: str, frame) -> None:
        try:
            self._encode_preview(cam, frame)
        finally:
            self._encoding.discard(cam)

    def _encode_preview(self, cam: str, frame) -> None:
        image = frame.image
        h, w = image.shape[:2]
        if w > self.cfg.preview_width:
            image = cv2.resize(image, (self.cfg.preview_width, round(h * self.cfg.preview_width / w)),
                               interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if ok:
            self.emit({"type": "preview", "camera": cam, "jpeg": jpeg.tobytes(), "t_frame": frame.received,
                       "width": w, "height": h})

    def report_stats(self) -> None:
        for cam, src in self.sources.items():
            arrivals = list(src.arrivals)
            gaps = [round((b - a) * 1000) for a, b in zip(arrivals[-5:], arrivals[-4:])]
            # frame rate = frames counted / time they took (a median gap would be skewed by jitter)
            span = arrivals[-1] - arrivals[0] if len(arrivals) >= 2 else 0.0
            fps = (len(arrivals) - 1) / span if span > 0 and time.perf_counter() - arrivals[-1] < 1.0 else 0.0
            msg = {"type": "stats", "camera": cam, "connected": bool(src.connected),
                   "frames_received": src.frames_received, "ndi_total": src.ndi_frames_total,
                   "ndi_dropped": src.ndi_frames_dropped, "gaps_ms": gaps, "fps": round(fps, 2),
                   "source": src.source_name}
            if isinstance(src, StreamSource):  # a network stream: its decoding is measured too
                msg.update(decode_ms=src.decode_ms, codec=src.codec, size=list(src.size))
                if src.last_error and src.last_error != self._stream_errors.get(cam):
                    self.emit({"type": "error", "camera": cam, "message": src.last_error})
                self._stream_errors[cam] = src.last_error
            self.emit(msg)

    def close(self) -> None:
        if self.gpu:
            self.gpu.close()
        for src in self.sources.values():
            try:
                src.stop()
            except Exception:  # noqa: BLE001 - shutting down anyway
                pass


def run_worker(cfg: dict, commands: mp.Queue, results: mp.Queue) -> None:
    """Process entry point."""
    def emit(msg: dict) -> None:
        try:
            results.put_nowait(msg)
        except queue.Full:
            pass  # the core is behind; newer results will follow

    loop = None
    try:
        config = WorkerConfig.from_dict(cfg)
        if config.gpu_libs:
            from trackxen.vision.runtime import use_gpu_libraries

            use_gpu_libraries(config.gpu_libs)
        loop = VisionLoop(config, emit)
        emit({"type": "ready", "engine": loop.detectors.engine, "pid": os.getpid(), "building": loop.detectors.building})
        next_stats = 0.0
        running = True
        while running:
            try:
                while True:
                    running = loop.command(commands.get_nowait()) and running
            except queue.Empty:
                pass
            if not loop.step():
                time.sleep(0.001)
            if time.monotonic() >= next_stats:
                next_stats = time.monotonic() + 1.0
                loop.report_stats()
    except Exception as e:  # noqa: BLE001 - tell the core why we died
        emit({"type": "error", "message": f"vision worker failed: {e}", "trace": traceback.format_exc()})
    finally:
        if loop:
            loop.close()


def start_worker(cfg: WorkerConfig) -> tuple[mp.Process, mp.Queue, mp.Queue]:
    ctx = mp.get_context("spawn")
    commands, results = ctx.Queue(), ctx.Queue(maxsize=256)
    proc = ctx.Process(target=run_worker, args=(cfg.to_dict(), commands, results), name="trackxen-vision",
                       daemon=True)
    proc.start()
    return proc, commands, results

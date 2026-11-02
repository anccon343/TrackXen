import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from trackxen.vision.pipeline import GpuStage, Job
from trackxen.vision.worker import VisionLoop


class FakeBatch:
    def __init__(self, frames):
        self.count, self.pre_ms, self.frames = len(frames), 0.1, frames


class FakeDetector:
    """prepare / infer / decode like PoseDetector; infer takes `gpu_s` and records the thread it ran on."""

    engine = "fake"

    def __init__(self, gpu_s: float = 0.0):
        self.gpu_s, self.threads, self.fail = gpu_s, set(), False

    def prepare(self, images):
        return FakeBatch(images)

    def infer(self, batch):
        self.threads.add(threading.current_thread().name)
        time.sleep(self.gpu_s)
        if self.fail:
            raise RuntimeError("GPU fell over")
        return [int(img[0, 0, 0]) for img in batch.frames]

    def decode(self, batch, out):
        return [[] for _ in out]


class FakeDetectors:
    def __init__(self, detector):
        self.detector = detector

    def get(self, frames=1):
        return self.detector


class FakeFrame:
    def __init__(self, number, size=(4, 4)):
        self.number, self.received, self.measured = number, time.monotonic(), time.perf_counter()
        self.image = np.full((size[1], size[0], 3), number % 256, np.uint8)


class FakeSource:
    def __init__(self):
        self.frame = None

    def latest(self):
        return self.frame


class FakeTracker:
    def update(self, people, t):
        return []


def make_loop(cameras: int, detector, pipeline: bool, max_fps: float = 0.0) -> tuple[VisionLoop, list]:
    loop = VisionLoop.__new__(VisionLoop)  # the detection loop only: fake sources and detector
    sent = []
    loop.emit = sent.append
    loop.sources = {f"cam{k}": FakeSource() for k in range(1, cameras + 1)}
    loop.trackers = {cam: FakeTracker() for cam in loop.sources}
    loop.last_number = {cam: 0 for cam in loop.sources}
    loop.previews = {cam: False for cam in loop.sources}
    loop.max_detect_fps, loop._last_detect, loop._tokens, loop._passed = max_fps, {}, {}, {}
    loop._allowance, loop.faces = set(), None
    loop._started, loop._starting, loop._start_results = set(loop.sources), {}, queue.SimpleQueue()
    loop._next_start_try, loop._start_wait, loop._sizes = {}, {}, {}
    loop.detectors, loop.batch = FakeDetectors(detector), 1 if cameras == 1 else 4
    loop.gpu = GpuStage() if pipeline else None
    loop._encoder, loop._encoding = ThreadPoolExecutor(1), set()
    return loop, sent


def drain(loop: VisionLoop) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        busy = loop.step()
        if not busy and not (loop.gpu and loop.gpu.pending):
            return
    raise AssertionError("the loop never went idle")


@pytest.mark.parametrize("pipeline", [False, True])
def test_every_frame_is_detected_once_and_in_order(pipeline):
    detector = FakeDetector(gpu_s=0.002)
    loop, sent = make_loop(4, detector, pipeline)
    for number in range(1, 31):
        for src in loop.sources.values():
            src.frame = FakeFrame(number)
        drain(loop)
    for cam in loop.sources:
        frames = [m["frame"] for m in sent if m["camera"] == cam]
        assert frames == list(range(1, 31))
        assert all(m["skipped"] == 0 for m in sent if m["camera"] == cam)
    assert all(0 < m["pipeline_ms"] < 2000 for m in sent)  # measured exactly (not in 15.6 ms ticks)
    assert any(m["pipeline_ms"] % 15.625 > 0.01 for m in sent)
    if pipeline:
        assert detector.threads == {"detect-gpu"}
        loop.gpu.close()


def test_pipeline_fills_a_batch_while_the_gpu_is_busy():
    detector = FakeDetector(gpu_s=0.05)
    loop, sent = make_loop(4, detector, pipeline=True)
    for src in loop.sources.values():
        src.frame = FakeFrame(1)
    loop.step()  # all four go to the GPU
    while not loop.gpu.has_room():  # (the GPU thread picks the batch up)
        time.sleep(0.001)
    loop.sources["cam1"].frame = FakeFrame(2)
    loop.step()  # one new frame while the GPU is busy: wait for the others instead of a batch of one
    assert loop.gpu.pending == 1
    for cam in ("cam2", "cam3", "cam4"):
        loop.sources[cam].frame = FakeFrame(2)
    loop.step()  # now a full batch is prepared while the first still runs
    assert loop.gpu.pending == 2
    drain(loop)
    assert [m["batch"] for m in sent] == [4] * 8
    loop.gpu.close()


def test_rate_limit_decides_once_per_frame_while_waiting():
    detector = FakeDetector(gpu_s=0.03)
    loop, sent = make_loop(4, detector, pipeline=True, max_fps=1000)  # (high: frame 2 is let through, then waits)
    asked = []
    allowed = loop._allowed
    loop._allowed = lambda cam, now: asked.append(cam) or allowed(cam, now)
    for src in loop.sources.values():
        src.frame = FakeFrame(1)
    loop.step()
    loop.sources["cam1"].frame = FakeFrame(2)
    for _ in range(20):  # looked at again and again while the GPU is busy
        loop.step()
    assert asked.count("cam1") == 2  # frame 1 and frame 2, each once
    drain(loop)
    loop.gpu.close()


def test_gpu_failure_reaches_the_worker():
    detector = FakeDetector()
    detector.fail = True
    loop, _ = make_loop(1, detector, pipeline=True)
    loop.sources["cam1"].frame = FakeFrame(1)
    with pytest.raises(RuntimeError, match="fell over"):
        drain(loop)
    loop.gpu.close()


def test_gpu_stage_keeps_order():
    stage = GpuStage()
    detector = FakeDetector(gpu_s=0.001)
    for n in range(5):
        while not stage.has_room():
            time.sleep(0.001)
        stage.submit(Job(detector, FakeBatch([np.full((1, 1, 3), n, np.uint8)]), payload=n))
    got = []
    while len(got) < 5:
        got += stage.collect(wait=0.5)
    assert [j.payload for j in got] == [0, 1, 2, 3, 4] and [j.out for j in got] == [[0], [1], [2], [3], [4]]
    stage.close()


class MissingSource(FakeSource):
    """A camera that is switched off: looking for it takes `seconds`, then fails."""

    def __init__(self, seconds: float):
        super().__init__()
        self.seconds, self.tries = seconds, 0

    def start(self, timeout):
        self.tries += 1
        time.sleep(self.seconds)
        raise LookupError("NDI source not found: CAM 2")


def wait_for_error(loop: VisionLoop, sent: list, count: int) -> None:
    deadline = time.monotonic() + 3.0
    while sum(1 for m in sent if m.get("type") == "error") < count:
        assert time.monotonic() < deadline, "no error reported"
        loop.step()
        time.sleep(0.01)


def test_a_missing_source_does_not_hold_up_the_other_cameras():
    loop, sent = make_loop(2, FakeDetector(), pipeline=False)
    loop._started = {"cam1"}
    missing = loop.sources["cam2"] = MissingSource(0.5)
    t0 = time.monotonic()
    for number in range(1, 6):
        loop.sources["cam1"].frame = FakeFrame(number)
        drain(loop)
    assert time.monotonic() - t0 < 0.3  # cam1's frames were not held up while cam2 was looked for
    assert [m["frame"] for m in sent if m.get("type") == "tracks"] == [1, 2, 3, 4, 5]
    wait_for_error(loop, sent, 1)
    assert missing.tries == 1 and "not found" in sent[-1]["message"] and sent[-1]["camera"] == "cam2"
    waits = [loop._next_start_try["cam2"] - time.monotonic()]
    missing.seconds = 0.0
    for count in range(2, 7):  # tried again later and later: 5, 10, 20, 40, 60 s
        loop._next_start_try["cam2"] = 0.0  # (as if that time had passed)
        wait_for_error(loop, sent, count)
        waits.append(loop._next_start_try["cam2"] - time.monotonic())
    assert missing.tries == 6
    assert [round(w) for w in waits] == [5, 10, 20, 40, 60, 60]


def test_a_source_found_later_is_used():
    loop, sent = make_loop(1, FakeDetector(), pipeline=False)
    loop._started = set()
    source = loop.sources["cam1"]
    source.start = lambda timeout: None
    deadline = time.monotonic() + 2.0
    while "cam1" not in loop._started:
        assert time.monotonic() < deadline
        loop.step()
        time.sleep(0.001)
    source.frame = FakeFrame(1)
    drain(loop)
    assert [m["frame"] for m in sent if m.get("type") == "tracks"] == [1]
    assert not any(m.get("type") == "error" for m in sent)


def test_new_picture_size_rescales_the_tracker():
    class Tracker(FakeTracker):
        def __init__(self):
            self.scaled = []

        def rescale(self, sx, sy):
            self.scaled.append((sx, sy))

    loop, _ = make_loop(1, FakeDetector(), pipeline=False)
    tracker = loop.trackers["cam1"] = Tracker()
    for number, size in enumerate([(1920, 1080), (1920, 1080), (1280, 720), (1280, 720)], start=1):
        loop.sources["cam1"].frame = FakeFrame(number, size)
        drain(loop)
    assert tracker.scaled == [(1280 / 1920, 720 / 1080)]

"""Two-stage detection: the GPU works on one batch while the CPU prepares the next one and
decodes, tracks and reports the one before.

Before, the vision loop did everything in a row (prepare, wait for the GPU, decode), so with
four cameras frames also waited for the CPU work around every batch. The GPU stage now runs on
a thread of its own (ONNX Runtime releases the GIL while a model runs). At most one prepared
batch waits for it, so frames never queue up and grow old.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Job:
    detector: Any  # yolo_pose.PoseDetector
    batch: Any  # yolo_pose.Batch
    payload: Any = None  # whatever the caller needs back with the result
    out: Any = None  # the model's output, once it ran
    infer_ms: float = 0.0
    error: Exception | None = None


class GpuStage:
    """Runs PoseDetector.infer on its own thread, one batch at a time, in the order submitted."""

    def __init__(self):
        self._todo: queue.Queue[Job | None] = queue.Queue(maxsize=1)
        self._done: queue.Queue[Job] = queue.Queue()
        self.pending = 0  # submitted and not collected yet
        self._thread = threading.Thread(target=self._run, name="detect-gpu", daemon=True)
        self._thread.start()

    def has_room(self) -> bool:
        """No prepared batch is waiting: the GPU took the last one (or has nothing to do)."""
        return self._todo.empty()

    def submit(self, job: Job) -> None:
        self.pending += 1
        self._todo.put(job)

    def collect(self, wait: float = 0.0) -> list[Job]:
        """Finished jobs, oldest first; waits up to `wait` seconds for the first one."""
        jobs = []
        try:
            jobs.append(self._done.get(timeout=wait) if wait > 0 else self._done.get_nowait())
            while True:
                jobs.append(self._done.get_nowait())
        except queue.Empty:
            pass
        self.pending -= len(jobs)
        return jobs

    def _run(self) -> None:
        while (job := self._todo.get()) is not None:
            t0 = time.perf_counter()
            try:
                job.out = job.detector.infer(job.batch)
            except Exception as e:  # noqa: BLE001 - handed back to the caller with the job
                job.error = e
            job.infer_ms = (time.perf_counter() - t0) * 1000
            self._done.put(job)

    def close(self) -> None:
        try:
            self._todo.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)


def throughput(detector, frames: list, seconds: float) -> float:
    """Images per second through both stages, the way the vision worker runs them (the next batch
    is prepared while the GPU runs the current one)."""
    stage = GpuStage()
    done, start = 0, time.perf_counter()
    try:
        while time.perf_counter() - start < seconds:
            if stage.has_room():
                stage.submit(Job(detector, detector.prepare(frames)))
            for job in stage.collect(wait=0.002):
                if job.error:
                    raise job.error
                detector.decode(job.batch, job.out)
                done += job.batch.count
        deadline = time.perf_counter() + 5.0
        while stage.pending and time.perf_counter() < deadline:  # the batches still on the GPU count too
            for job in stage.collect(wait=0.5):
                done += job.batch.count
        return done / (time.perf_counter() - start)
    finally:
        stage.close()

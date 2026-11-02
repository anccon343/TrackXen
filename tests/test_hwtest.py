import time
from types import SimpleNamespace

import pytest

from trackxen.core import hwtest
from trackxen.core.performance import PRESETS


class FakeDetector:
    """A fixed batch-of-4 model that opens only on the engines in `works` (others fall back, like
    ONNX Runtime without the NVIDIA libraries), and takes `slow` seconds on the n-th detect()."""

    works = {"cpu"}
    slow: dict[int, float] = {}
    made: list["FakeDetector"] = []

    def __init__(self, model_path, cache_dir=None, engines=None, conf=0.4):
        self.engines = tuple(engines)
        self.engine = next((e for e in self.engines if e in self.works), None)
        if self.engine is None:
            raise RuntimeError(f"no inference engine could load {model_path}")
        self.batch, self.frames, self.calls = 4, [], 0
        self.last_timing = {"pre_ms": 0.1, "infer_ms": 1.0, "post_ms": 0.1}
        FakeDetector.made.append(self)

    def prepare(self, frames):
        self.frames.append(len(frames))
        return SimpleNamespace(count=len(frames))

    def infer(self, batch):
        time.sleep(0.001)

    def decode(self, batch, out):
        return []

    def detect(self, frames):
        self.calls += 1
        self.infer(self.prepare(frames))
        time.sleep(self.slow.get(self.calls, 0.0))
        return []


@pytest.fixture
def fake(tmp_path, monkeypatch):
    from trackxen.vision import yolo_pose

    monkeypatch.setattr(yolo_pose, "PoseDetector", FakeDetector)
    monkeypatch.setattr(FakeDetector, "made", [])
    monkeypatch.setattr(FakeDetector, "slow", {})
    # what ONNX Runtime's GPU build lists, whether or not this PC can use them
    monkeypatch.setattr(hwtest, "available_engines", lambda: ["tensorrt", "cuda", "cpu"])
    monkeypatch.setattr(hwtest, "measure_cpu_work", lambda frame: {"frame_copy_ms": 1.0, "preview_jpeg_ms": 2.0})
    for p in PRESETS.values():
        for batch in (1, 4):
            (tmp_path / p.model_file(batch)).write_bytes(b"")
    return tmp_path


def test_a_pc_without_nvidia_is_measured_on_the_processor(fake):
    for quick in (True, False):
        report = hwtest.run(fake, cameras=4, tensorrt=not quick, seconds=0.05, progress=lambda line: None)
        assert report["engine"] == "cpu" and len(report["models"]) == 4
        assert all(r["engine"] == "cpu" for r in report["models"].values())
        assert report["recommendation"]["mode"] != "insufficient"


def test_an_engine_that_doesnt_load_isnt_tried_for_every_model(fake, monkeypatch):
    monkeypatch.setattr(FakeDetector, "works", {"cuda", "cpu"})  # TensorRT can't build here
    lines = []
    report = hwtest.run(fake, cameras=4, seconds=0.05, progress=lines.append)
    assert report["engine"] == "cuda" and any("measured on cuda" in line for line in lines)
    assert [d.engines[0] for d in FakeDetector.made] == ["tensorrt", "cuda", "cuda", "cuda"]


def test_a_batch_holds_one_frame_per_camera(fake):
    for cameras, frames in ((1, 1), (2, 2), (3, 3), (4, 4)):
        FakeDetector.made.clear()
        report = hwtest.run(fake, cameras=cameras, seconds=0.05, progress=lambda line: None)
        assert {n for d in FakeDetector.made for n in d.frames} == {frames}
        r = next(iter(report["models"].values()))
        assert r["images_per_s_one_at_a_time"] == pytest.approx(frames / (r["batch_ms_p50"] / 1000), rel=0.05)


def test_the_95th_percentile_shows_the_one_slow_batch(fake, monkeypatch):
    monkeypatch.setattr(FakeDetector, "slow", {3 + 5: 0.2})  # after 3 warm-up runs, the 5th of 10 measured
    r = hwtest.measure_preset_model(fake / "yolo26n-pose-640-b4.onnx", ("cpu",), 4, hwtest.test_frame(), 0.0,
                                    fake / "cache")
    assert r["runs"] == 10 and r["batch_ms_p95"] >= 200

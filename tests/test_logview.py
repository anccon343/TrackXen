import json
import tracemalloc

from trackxen.core import logview


def write_samples(path, n: int, pad: int = 0, end: str = "\n") -> None:
    sample = {"cpu_percent": 0, "gpus": [{"load_percent": 50}], "app": {"cam1.detections_per_s": 30}, "pad": "x" * pad}
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps({**sample, "time": f"t{i}", "cpu_percent": i}) for i in range(n)) + end)


def test_stats_returns_at_most_max_points(tmp_path):
    for n, end in ((2999, "\n"), (4499, ""), (1500, "\n"), (7, "")):
        write_samples(tmp_path / "stats-20261101.jsonl", n, end=end)
        points = logview.stats(tmp_path, "20261101", max_points=1500)
        assert len(points) == min(n, 1500) and points[0]["cpu"] == 0


def test_stats_reads_a_big_day_without_loading_it_whole(tmp_path):
    write_samples(tmp_path / "stats-20261101.jsonl", 4000, pad=2500)  # about 10 MB, like an hour with 4 cameras
    tracemalloc.start()
    try:
        points = logview.stats(tmp_path, "20261101", max_points=100)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(points) == 100 and points[1]["cpu"] == 40
    assert peak < 4_000_000  # the file is read a piece at a time


def test_stats_of_a_missing_or_empty_day(tmp_path):
    assert logview.stats(tmp_path, "20261101") == []
    (tmp_path / "stats-20261101.jsonl").write_text("", encoding="utf-8")
    assert logview.stats(tmp_path, "20261101") == []

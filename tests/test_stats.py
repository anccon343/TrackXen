import datetime as dt
import json

from trackxen.core import stats


class FakeSampler:
    def __init__(self, samples):
        self.samples = list(samples)

    def sample(self) -> dict:
        return dict(self.samples.pop(0))


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_counters_report_rates_and_gauges():
    c = stats.Counters()
    c.add("cam1.frames", 30)
    c.add("cam1.frames", 30)
    c.set("cam1.delay_ms", 84)
    snap = c.take(2.0)
    assert snap == {"cam1.delay_ms": 84, "cam1.frames_per_s": 30.0}
    assert c.take(1.0) == {"cam1.delay_ms": 84}


def test_observations_report_percentiles():
    c = stats.Counters()
    for v in range(1, 101):  # 1..100 ms
        c.observe("cam1.detect_ms", float(v))
    snap = c.take(1.0)
    assert snap["cam1.detect_ms_p50"] == 50 and snap["cam1.detect_ms_p95"] == 95
    assert snap["cam1.detect_ms_max"] == 100 and snap["cam1.detect_ms_n"] == 100
    assert "cam1.detect_ms_p50" not in c.take(1.0)


def test_gpu_engine_counter_parsing():
    from trackxen.core.gpu_counters import parse_engine_counters

    values = {
        "pid_10_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 30.0,
        "pid_11_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 25.0,  # same engine, other process
        "pid_10_luid_0x0_0x1_phys_0_eng_3_engtype_Copy": 5.0,
        "pid_12_luid_0x0_0x2_phys_0_eng_0_engtype_3D": 1.0,  # another adapter
    }
    loads = {g.adapter: g for g in parse_engine_counters(values, app_pids={11})}
    assert loads["0x0_0x1"].load_percent == 55.0  # busiest engine, summed over processes
    assert loads["0x0_0x1"].engine_types == {"3D": 55.0, "Copy": 5.0}
    assert loads["0x0_0x1"].app_load_percent == 25.0
    assert loads["0x0_0x2"].load_percent == 1.0


def test_first_tick_rate_is_not_inflated(tmp_path):
    collector = stats.StatsCollector(stats.StatsLog(tmp_path), sampler=FakeSampler([{"gpus": []}]))
    collector.counters.add("cam1.frames", 30)
    sample = collector.tick()  # immediately after start
    assert sample["app"]["cam1.frames_per_s"] <= 60


def test_problem_rules():
    limits = stats.Limits(expected_fps={"cam1": 30, "cam2": 30, "cam3": 30})
    sample = {
        "cpu_percent": 50, "memory_percent": 50,
        "gpus": [{"load_percent": 99, "temperature_c": 70}],
        "app": {"cam1.detections_per_s": 29.9, "cam2.detections_per_s": 20.0},
    }
    found = {(p.source, p.level) for p in stats.check_problems(sample, limits)}
    assert found == {("gpu", "warning"), ("cam2", "warning"), ("cam3", "error")}


def test_collector_logs_samples_and_lasting_problem_changes(tmp_path):
    ok = {"cpu_percent": 10, "memory_percent": 20, "gpus": []}
    busy = {"cpu_percent": 97, "memory_percent": 20, "gpus": []}
    seq = [ok, busy, busy, ok] + [busy] * 4 + [ok, busy] + [ok] * 6  # a blip, then a lasting problem
    log = stats.StatsLog(tmp_path)
    collector = stats.StatsCollector(log, sampler=FakeSampler(seq))
    shown = []
    for _ in seq:
        collector.tick()
        shown.append(bool(collector.problems))
    day = dt.datetime.now().strftime("%Y%m%d")
    samples = read_lines(tmp_path / f"stats-{day}.jsonl")
    events = read_lines(tmp_path / f"events-{day}.jsonl")
    assert len(samples) == len(seq) and samples[1]["cpu_percent"] == 97
    assert [e["kind"] for e in events] == ["problem", "problem_cleared"]  # the 2-second blip isn't one
    assert events[0]["source"] == "pc" and events[0]["level"] == "warning"
    assert shown == [False] * 6 + [True] * 8 + [False] * 2  # shown after 3 s, kept through a short dip, gone after 5 s


def test_text_log_has_events_and_summaries(tmp_path):
    sampler = FakeSampler([{"cpu_percent": 12, "memory_percent": 40, "net_rx_mbps": 120.3,
                            "gpus": [{"load_percent": 31, "memory_used_mb": 2458, "memory_total_mb": 16376,
                                      "temperature_c": 41}]}] * 3)
    log = stats.StatsLog(tmp_path, text_every=3600)
    collector = stats.StatsCollector(log, sampler=sampler)
    collector.counters.set("cam1.detect_ms", 5.6)
    log.event("target", "locked track 4", source="cam1")
    for _ in range(3):
        collector.tick()
    lines = log.text_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the event and one summary (summaries every text_every seconds)
    assert "INFO" in lines[0] and "cam1" in lines[0] and "target: locked track 4" in lines[0]
    assert "STATS" in lines[1] and "CPU 12%" in lines[1] and "GPU 31% 2.4/16.0 GB 41C" in lines[1]
    assert "cam1: detect_ms 5.6" in lines[1]


def test_old_logs_are_pruned(tmp_path):
    old = tmp_path / "stats-20200101.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    keep = tmp_path / f"stats-{dt.datetime.now():%Y%m%d}.jsonl"
    keep.write_text("{}\n", encoding="utf-8")
    stats.StatsLog(tmp_path, keep_days=30)
    assert not old.exists() and keep.exists()


def test_real_system_sampler():
    sample = stats.SystemSampler().sample()
    assert 0 <= sample["cpu_percent"] <= 100
    assert sample["app_memory_mb"] > 0
    for gpu in sample["gpus"]:
        assert 0 <= gpu["load_percent"] <= 100 and gpu["memory_total_mb"] > 0


def test_log_viewer_reads_days_events_and_thinned_samples(tmp_path):
    import json

    from trackxen.core import logview

    (tmp_path / "events-20261101.jsonl").write_text(
        '{"time":"2026-11-01T10:00:00.000","level":"info","kind":"target","message":"target locked"}\n{"broken\n',
        encoding="utf-8")
    samples = [{"time": f"2026-11-01T10:00:{i:02d}", "cpu_percent": i, "gpus": [{"load_percent": 50}],
                "app": {"cam1.detections_per_s": 30, "cam1.detect_ms_p50": 9.5, "app.control_loop_ms_p50": 0.1}}
               for i in range(60)]
    (tmp_path / "stats-20261101.jsonl").write_text("\n".join(json.dumps(s) for s in samples), encoding="utf-8")
    assert logview.days(tmp_path)[0]["day"] == "20261101"
    assert [e["message"] for e in logview.events(tmp_path, "20261101")] == ["target locked"]  # cut-off line skipped
    points = logview.stats(tmp_path, "20261101", max_points=20)
    assert len(points) == 20 and points[1]["cpu"] == 3
    assert points[0]["cams"] == {"cam1": {"detections_per_s": 30, "detect_ms_p50": 9.5}}
    import pytest

    with pytest.raises(ValueError):
        logview.file_of(tmp_path, "../../secret", "text")


def test_gpu_load_and_temperature_are_separate_problems(tmp_path):
    gpu = {"load_percent": 98, "memory_used_mb": 1, "memory_total_mb": 2, "temperature_c": 60}
    busy = {"cpu_percent": 10, "memory_percent": 20, "gpus": [gpu]}
    hot_and_busy = {**busy, "gpus": [{**gpu, "temperature_c": 88}]}
    seq = [busy] * 4 + [hot_and_busy] * 4
    collector = stats.StatsCollector(stats.StatsLog(tmp_path), sampler=FakeSampler(seq))
    for _ in seq:
        collector.tick()
    assert [p.message for p in collector.problems] == ["GPU at 98 % load", "GPU at 88 °C"]
    events = read_lines(tmp_path / f"events-{dt.datetime.now():%Y%m%d}.jsonl")
    assert [e["message"] for e in events] == ["GPU at 98 % load", "GPU at 88 °C"]  # the second one is logged too


def test_two_gpus_have_their_own_problems():
    gpu = {"load_percent": 99, "memory_used_mb": 1, "memory_total_mb": 2, "temperature_c": 50}
    found = [p.message for p in stats.check_problems({"gpus": [gpu, gpu]}, stats.Limits())]
    assert found == ["GPU at 99 % load", "GPU 2 at 99 % load"]


def test_a_problem_getting_worse_is_logged_once(tmp_path):
    rates = [20] * 3 + [5, 20, 5, 20, 5]  # a warning, then flapping between error and warning
    ok = {"cpu_percent": 10, "memory_percent": 20, "gpus": []}
    collector = stats.StatsCollector(stats.StatsLog(tmp_path), limits=stats.Limits(expected_fps={"cam1": 30}),
                                     sampler=FakeSampler([ok] * len(rates)))
    for rate in rates:
        collector.counters.add("cam1.detections", rate)
        collector._last -= 1.0  # a second per tick
        collector.tick()
    events = read_lines(tmp_path / f"events-{dt.datetime.now():%Y%m%d}.jsonl")
    assert [(e["kind"], e["level"]) for e in events] == [("problem", "warning"), ("problem", "error")]


def test_a_failed_log_write_is_reported_not_raised(tmp_path):
    import shutil

    log = stats.StatsLog(tmp_path / "logs")
    shutil.rmtree(tmp_path / "logs")  # (or the disk full, or the file locked by another program)
    log.event("camera", "target lost", source="cam1")  # must not stop the control loop or the watchdog
    assert log.failing
    log.event("camera", "target found", source="cam1")  # the folder was made again
    assert "target found" in log.text_path.read_text(encoding="utf-8")
    (tmp_path / "logs" / f"stats-{dt.datetime.now():%Y%m%d}.jsonl").mkdir()  # samples can't be written
    collector = stats.StatsCollector(log, sampler=FakeSampler([{"cpu_percent": 1, "gpus": []}] * 3))
    for _ in range(3):
        collector.tick()
    assert [p.source for p in collector.problems] == ["pc"] and "log" in collector.problems[0].message


def test_old_logs_are_pruned_while_running(tmp_path, monkeypatch):
    log = stats.StatsLog(tmp_path, keep_days=30)
    old = tmp_path / "stats-20200101.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(log, "_day", lambda: "20991231")  # the next day began
    log.sample({"cpu_percent": 1})
    assert not old.exists()


def test_names_with_line_breaks_stay_on_one_text_log_line(tmp_path):
    log = stats.StatsLog(tmp_path)
    log.event("faces", "face registered: Bob\n2026-11-02 06:00:00.000  ERROR   cam1 forged\u2028x", source="cam\r1")
    log.event("faces", "face registered: \ud800", source="cam1")  # (a lone surrogate, which JSON allows)
    lines = log.text_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and "Bob\\n2026" in lines[0] and "cam\\r1" in lines[0]


def test_forgotten_gauges_are_no_longer_reported():
    c = stats.Counters()
    c.set("cam1.source_fps", 29.97)
    c.set("cam12.source_fps", 25)
    c.forget("cam1.")
    assert c.take(1.0) == {"cam12.source_fps": 25}

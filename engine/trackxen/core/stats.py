"""Statistics: PC load plus the app's own counters, sampled once a second.

Written automatically to the log folder:
- `TrackXen-YYYYMMDD.log`: readable text log with every event and a one-line
  statistics summary every few seconds
- `stats-YYYYMMDD.jsonl` / `events-YYYYMMDD.jsonl`: the same data for graphs and tools
`check_problems()` turns a sample into the OK / warning / error lights the UI shows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

try:
    import pynvml  # from the nvidia-ml-py package
except ImportError:  # no NVIDIA driver/library: GPU numbers are simply missing
    pynvml = None


class SystemSampler:
    """Measured PC load. Sources are documented in docs/research.md section 3.5:

    - CPU, memory, network: psutil (system-wide, and summed over the app's processes)
    - GPU load, temperature, video memory: NVIDIA NVML (sees our CUDA/TensorRT work)
    - 3D/video engine load and our video memory: Windows' GPU counters (what Task Manager uses)
    """

    def __init__(self, app_pids: set[int] | None = None):
        from trackxen.core.gpu_counters import GpuCounters

        self.app_pids = app_pids if app_pids is not None else {psutil.Process().pid}
        self._procs: dict[int, psutil.Process] = {}
        psutil.cpu_percent(None)
        self._net = psutil.net_io_counters()
        self._net_time = time.monotonic()
        self._nvml_time = int(time.time() * 1e6)
        self._gpus = []
        if pynvml:
            try:
                pynvml.nvmlInit()
                self._gpus = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
            except Exception:  # noqa: BLE001 - NVML missing or no NVIDIA GPU
                self._gpus = []
        self._engines = GpuCounters()

    def _app_processes(self) -> list[psutil.Process]:
        for pid in list(self._procs):
            if pid not in self.app_pids:
                del self._procs[pid]
        for pid in self.app_pids:
            if pid not in self._procs:
                try:
                    self._procs[pid] = psutil.Process(pid)
                    self._procs[pid].cpu_percent(None)  # first call only starts the measurement
                except psutil.Error:
                    continue
        return list(self._procs.values())

    def sample(self) -> dict:
        now = time.monotonic()
        net = psutil.net_io_counters()
        elapsed = max(now - self._net_time, 1e-6)
        rx = (net.bytes_recv - self._net.bytes_recv) * 8 / elapsed / 1e6
        tx = (net.bytes_sent - self._net.bytes_sent) * 8 / elapsed / 1e6
        self._net, self._net_time = net, now
        app_cpu = app_mem = 0.0
        for proc in self._app_processes():
            try:
                app_cpu += proc.cpu_percent(None)
                app_mem += proc.memory_info().rss
            except psutil.Error:
                continue
        out = {
            "cpu_percent": psutil.cpu_percent(None),
            "memory_percent": psutil.virtual_memory().percent,
            "app_cpu_percent": round(app_cpu / psutil.cpu_count(), 1),
            "app_memory_mb": round(app_mem / 2**20),
            "net_rx_mbps": round(rx, 1),
            "net_tx_mbps": round(tx, 1),
            "gpus": [self._nvml_sample(h) for h in self._gpus],
            "gpu_engines": [
                {"adapter": g.adapter, "load_percent": round(g.load_percent, 1),
                 "engine_types": {k: round(v, 1) for k, v in g.engine_types.items() if v > 0},
                 "app_memory_mb": round(g.app_memory_mb)}
                for g in self._engines.sample(self.app_pids)],
        }
        out["gpus"] = [g for g in out["gpus"] if g]
        self._nvml_time = int(time.time() * 1e6)
        return out

    def _nvml_sample(self, h) -> dict | None:
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            gpu = {
                "name": pynvml.nvmlDeviceGetName(h),
                "load_percent": pynvml.nvmlDeviceGetUtilizationRates(h).gpu,
                "memory_used_mb": mem.used // 2**20,
                "memory_total_mb": mem.total // 2**20,
                "temperature_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
            }
        except Exception:  # noqa: BLE001 - a GPU read can fail transiently
            return None
        try:  # our processes' share: NVML returns several samples per process for the window
            per_pid: dict[int, list[int]] = {}
            for s in pynvml.nvmlDeviceGetProcessUtilization(h, self._nvml_time):
                per_pid.setdefault(s.pid, []).append(s.smUtil)
            gpu["app_load_percent"] = round(sum(sum(v) / len(v) for pid, v in per_pid.items()
                                                if pid in self.app_pids))
        except Exception:  # noqa: BLE001 - no samples in the window, or not supported
            gpu["app_load_percent"] = 0
        return gpu


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, math.ceil(p / 100 * len(sorted_values)) - 1))
    return sorted_values[k]


class Counters:
    """App counters, all measured by the code that does the work:

    - add("cam1.frames")        -> reported as cam1.frames_per_s
    - set("cam1.state", 2)      -> reported as the latest value
    - observe("cam1.detect_ms", 5.4) -> reported as _p50, _p95, _max and _n for the interval
    """

    MAX_OBSERVATIONS = 5000

    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._observed: dict[str, list[float]] = {}

    def add(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + amount

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def forget(self, prefix: str) -> None:
        """Drop the gauges whose names start with `prefix` (e.g. "cam2." for a camera that was removed)."""
        with self._lock:
            self._gauges = {k: v for k, v in self._gauges.items() if not k.startswith(prefix)}

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            values = self._observed.setdefault(name, [])
            if len(values) < self.MAX_OBSERVATIONS:
                values.append(value)

    def take(self, seconds: float) -> dict[str, float]:
        """Rates and percentiles since the last call, plus current gauges; resets the interval."""
        with self._lock:
            counts, self._counts = self._counts, {}
            observed, self._observed = self._observed, {}
            gauges = dict(self._gauges)
        out = dict(gauges)
        out.update({f"{k}_per_s": round(v / max(seconds, 1e-6), 2) for k, v in counts.items()})
        for name, values in observed.items():
            values.sort()
            out.update({f"{name}_p50": round(percentile(values, 50), 2),
                        f"{name}_p95": round(percentile(values, 95), 2),
                        f"{name}_max": round(values[-1], 2), f"{name}_n": len(values)})
        return out


def format_summary(sample: dict) -> str:
    """One readable line: PC load, then the app counters grouped by camera/source."""
    parts = [f"CPU {sample.get('cpu_percent', 0):.0f}%", f"RAM {sample.get('memory_percent', 0):.0f}%"]
    for i, gpu in enumerate(sample.get("gpus", [])):
        parts.append(f"GPU{i if i else ''} {gpu['load_percent']}% {gpu['memory_used_mb'] / 1024:.1f}/"
                     f"{gpu['memory_total_mb'] / 1024:.1f} GB {gpu['temperature_c']}C"
                     + (f" (app {gpu['app_load_percent']}%)" if gpu.get("app_load_percent") else ""))
    for eng in sample.get("gpu_engines", []):
        if eng.get("app_memory_mb") or eng.get("load_percent", 0) >= 1:
            busiest = max(eng["engine_types"].items(), key=lambda kv: kv[1], default=("-", 0))
            parts.append(f"engines {busiest[0]} {busiest[1]:.0f}%, app VRAM {eng['app_memory_mb']} MB")
    parts.append(f"NET in {sample.get('net_rx_mbps', 0):.1f} out {sample.get('net_tx_mbps', 0):.1f} Mbit/s")
    groups: dict[str, list[str]] = {}
    for key, value in sorted(sample.get("app", {}).items()):
        group, _, name = key.partition(".")
        if not name:
            group, name = "app", key
        text = f"{value:.1f}" if isinstance(value, float) else str(value)
        groups.setdefault(group, []).append(f"{name.replace('_per_s', '/s')} {text}")
    return "  ".join(parts) + "".join(f" | {g}: {', '.join(v)}" for g, v in groups.items())


class StatsLog:
    """Daily log files (text + JSON lines) for samples and events, keeping `keep_days` days."""

    def __init__(self, folder: Path, keep_days: int = 30, text_every: float = 10.0,
                 app_name: str = "TrackXen"):
        self.folder, self.keep_days = folder, keep_days
        self.text_every, self.app_name = text_every, app_name
        self._last_text = float("-inf")
        self._lock = threading.Lock()
        self._pruned = ""  # the day old files were last removed
        self.error = ""  # why the last failed write failed
        self._failed_at = float("-inf")
        folder.mkdir(parents=True, exist_ok=True)
        self.prune()

    @property
    def failing(self) -> str:
        """Why a write failed, if one did in the last half minute ("" = the files are being written)."""
        return self.error if time.monotonic() - self._failed_at < 30 else ""

    def _write(self, name: str, line: str) -> None:
        """Never raises: the control loop and the watchdog log too, and a full disk, a deleted folder
        or a file locked by another program must not stop them (a problem light shows it instead)."""
        with self._lock:
            try:
                with (self.folder / name).open("a", encoding="utf-8", errors="backslashreplace") as f:
                    f.write(line + "\n")
            except OSError as e:
                self.error, self._failed_at = str(e) or type(e).__name__, time.monotonic()
                try:
                    self.folder.mkdir(parents=True, exist_ok=True)  # deleted: the next line can go in
                except OSError:
                    pass

    def _day(self) -> str:
        return dt.datetime.now().strftime("%Y%m%d")

    @property
    def text_path(self) -> Path:
        return self.folder / f"{self.app_name}-{self._day()}.log"

    def _text(self, level: str, source: str, message: str) -> None:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self._write(self.text_path.name, f"{stamp}  {level.upper():7s} {_one_line(source):6s} {_one_line(message)}")

    def sample(self, record: dict) -> None:
        if self._day() != self._pruned:  # (the app may run for weeks)
            self.prune()
        self._write(f"stats-{self._day()}.jsonl", json.dumps(
            {"time": dt.datetime.now().isoformat(timespec="seconds"), **record}, separators=(",", ":")))
        now = time.monotonic()
        if now - self._last_text >= self.text_every:
            self._last_text = now
            self._text("stats", "", format_summary(record))

    def event(self, kind: str, message: str, level: str = "info", **fields) -> None:
        self._write(f"events-{self._day()}.jsonl", json.dumps(
            {"time": dt.datetime.now().isoformat(timespec="milliseconds"), "level": level, "kind": kind,
             "message": message, **fields}, separators=(",", ":")))
        self._text(level, str(fields.get("source", "")), f"{kind}: {message}")

    def prune(self) -> None:
        """Remove the files older than `keep_days` (at the start, then once a day)."""
        self._pruned = self._day()
        cutoff = dt.datetime.now() - dt.timedelta(days=self.keep_days)
        try:
            paths = [*self.folder.glob("*.jsonl"), *self.folder.glob("*.log")]
        except OSError:
            return
        for path in paths:
            try:
                day = dt.datetime.strptime(path.stem.split("-")[-1], "%Y%m%d")
            except ValueError:
                continue
            if day < cutoff:
                try:
                    path.unlink(missing_ok=True)
                except OSError:  # e.g. open in another program: next time
                    pass


def _one_line(text: str) -> str:
    """Line breaks and other control characters (in a face or camera name) written as escapes, so
    every text log line is one real entry."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", lambda m: m.group().encode("unicode_escape").decode("ascii"),
                  text)


@dataclass
class Problem:
    level: str  # "warning" or "error"
    source: str  # "pc", "gpu", "cam1", "mixer", "vmix", ...
    message: str
    kind: str = ""  # what it is without its numbers ("GPU load"); "" = the message's first word


@dataclass
class Limits:
    gpu_load_warning: float = 95.0
    gpu_temp_warning: float = 85.0
    cpu_warning: float = 90.0
    memory_warning: float = 90.0
    fps_warning_ratio: float = 0.8  # below 80 % of the expected frame rate
    fps_error_ratio: float = 0.4
    expected_fps: dict[str, float] = field(default_factory=dict)  # e.g. {"cam1": 30}


def check_problems(sample: dict, limits: Limits) -> list[Problem]:
    """Problems in one sample, for the UI's lights and the event log."""
    problems = []
    if sample.get("cpu_percent", 0) >= limits.cpu_warning:
        problems.append(Problem("warning", "pc", f"CPU at {sample['cpu_percent']:.0f} %"))
    if sample.get("memory_percent", 0) >= limits.memory_warning:
        problems.append(Problem("warning", "pc", f"memory at {sample['memory_percent']:.0f} %"))
    for i, gpu in enumerate(sample.get("gpus", [])):
        name = f"GPU {i + 1}" if i else "GPU"
        if gpu["load_percent"] >= limits.gpu_load_warning:
            problems.append(Problem("warning", "gpu", f"{name} at {gpu['load_percent']} % load", f"{name} load"))
        if gpu["temperature_c"] >= limits.gpu_temp_warning:
            problems.append(Problem("warning", "gpu", f"{name} at {gpu['temperature_c']} °C", f"{name} temperature"))
    app = sample.get("app", {})
    for cam, expected in limits.expected_fps.items():
        # detections per second against the camera's measured frame rate
        fps = app.get(f"{cam}.detections_per_s")
        if fps is None or fps < expected * limits.fps_error_ratio:
            problems.append(Problem("error", cam, f"tracking {fps or 0:.0f} of {expected:.0f} frames/s"))
        elif fps < expected * limits.fps_warning_ratio:
            problems.append(Problem("warning", cam, f"tracking {fps:.0f} of {expected:.0f} frames/s"))
    return problems


RAISE_AFTER, CLEAR_AFTER = 3, 5  # samples (seconds) a problem must last to show, and be gone to clear


class StatsCollector:
    """Samples once a second, logs it, and keeps the latest sample and problems for the UI.

    Problems have hysteresis: one that comes and goes every second (a frame rate hovering at the
    limit) would otherwise flood the log and blink the problem light."""

    def __init__(self, log: StatsLog, counters: Counters | None = None, limits: Limits | None = None,
                 sampler: SystemSampler | None = None,
                 on_sample: Callable[[dict, list[Problem]], None] | None = None, interval: float = 1.0):
        self.log, self.counters = log, counters or Counters()
        self.limits, self.sampler = limits or Limits(), sampler or SystemSampler()
        self.on_sample, self.interval = on_sample, interval
        self.latest: dict = {}
        self.problems: list[Problem] = []
        self.extra_problems: Callable[[], list[Problem]] | None = None  # e.g. the app's camera and vMix connections
        self._active: set[tuple[str, str]] = set()
        self._present: dict[tuple[str, str], int] = {}  # consecutive samples with the problem
        self._absent: dict[tuple[str, str], int] = {}  # consecutive samples without an active problem
        self._message: dict[tuple[str, str], Problem] = {}  # newest form of each active problem
        self._logged: dict[tuple[str, str], str] = {}  # the level each active problem was logged at
        self._last = time.monotonic()

    def tick(self) -> dict:
        now = time.monotonic()
        sample = self.sampler.sample()
        # Never divide counts by less than half an interval (e.g. a tick right after start)
        sample["app"] = self.counters.take(max(now - self._last, 0.5 * self.interval))
        self._last = now
        found = check_problems(sample, self.limits)
        if self.log.failing:
            found.append(Problem("warning", "pc", f"log files can't be written: {self.log.failing}", "log files"))
        if self.extra_problems:  # (the app's connection lights: cameras, FreeD, vMix, mixer)
            found += self.extra_problems()
        problems = self._settle(found)
        self.latest, self.problems = sample, problems
        self.log.sample(sample)
        if self.on_sample:
            self.on_sample(sample, problems)
        return sample

    def _settle(self, problems: list[Problem]) -> list[Problem]:
        """The problems to show: logged when they start (after RAISE_AFTER samples in a row) and
        when they clear (after CLEAR_AFTER samples without them), not every second. A problem is
        known by its source and kind, so its numbers can change (and a warning can become an
        error, logged once more) without it counting as a new one."""
        current = {(p.source, p.kind or p.message.split(" ")[0]): p for p in problems}
        for key in list(self._present):
            if key not in current:
                del self._present[key]
        for key, p in current.items():
            self._present[key] = self._present.get(key, 0) + 1
            self._absent.pop(key, None)
            if key in self._active:
                self._message[key] = p
                if p.level == "error" and self._logged[key] != "error":
                    self._logged[key] = p.level
                    self.log.event("problem", p.message, level=p.level, source=p.source)
            elif self._present[key] >= RAISE_AFTER:
                self._active.add(key)
                self._message[key], self._logged[key] = p, p.level
                self.log.event("problem", p.message, level=p.level, source=p.source)
        for key in list(self._active - current.keys()):
            self._absent[key] = self._absent.get(key, 0) + 1
            if self._absent[key] >= CLEAR_AFTER:
                self._active.discard(key)
                del self._absent[key], self._message[key], self._logged[key]
                self.log.event("problem_cleared", f"{key[1]} back to normal", source=key[0])
        return [self._message[key] for key in sorted(self._active)]

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            self.tick()

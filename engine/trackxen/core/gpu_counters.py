"""GPU load the way Windows Task Manager measures it: the "GPU Engine" performance counters.

Each counter instance is one engine used by one process, e.g.
`pid_1234_luid_0x0_0x16133_phys_0_eng_3_engtype_Compute_0`. An engine's load is the sum
over processes (capped at 100 %); the GPU's load is its busiest engine, as in Task
Manager. Per-process load and dedicated video memory come from the same counters, so
the app can report its own share. Windows only; on other systems `available` is False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import win32pdh  # pywin32
except ImportError:
    win32pdh = None

_ENGINE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(\d+)_eng_(\d+)_engtype_(.*)")
_PROCESS_MEMORY = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(\d+)")


@dataclass
class GpuLoad:
    adapter: str  # LUID of the adapter
    load_percent: float  # busiest engine, like Task Manager
    engine_types: dict[str, float] = field(default_factory=dict)  # e.g. {"3D": 12.0, "Compute_0": 40.0}
    app_load_percent: float = 0.0  # this app's busiest engine
    app_memory_mb: float = 0.0  # this app's dedicated video memory


def parse_engine_counters(values: dict[str, float], app_pids: set[int]) -> list[GpuLoad]:
    """Turn raw "GPU Engine" counter values into per-adapter loads."""
    engines: dict[tuple[str, str, str], float] = {}  # (luid, phys+eng, type) -> load
    app_engines: dict[tuple[str, str, str], float] = {}
    for name, value in values.items():
        m = _ENGINE.match(name)
        if not m:
            continue
        pid, luid, phys, eng, etype = int(m[1]), m[2], m[3], m[4], m[5]
        key = (luid, f"{phys}.{eng}", etype)
        engines[key] = engines.get(key, 0.0) + value
        if pid in app_pids:
            app_engines[key] = app_engines.get(key, 0.0) + value
    loads: dict[str, GpuLoad] = {}
    for (luid, _, etype), value in engines.items():
        value = min(100.0, value)
        gpu = loads.setdefault(luid, GpuLoad(adapter=luid, load_percent=0.0))
        gpu.load_percent = max(gpu.load_percent, value)
        gpu.engine_types[etype] = max(gpu.engine_types.get(etype, 0.0), value)
    for (luid, _, _), value in app_engines.items():
        if luid in loads:
            loads[luid].app_load_percent = max(loads[luid].app_load_percent, min(100.0, value))
    return list(loads.values())


class GpuCounters:
    """Reads the counters; call `sample()` about once a second."""

    def __init__(self):
        self.available = False
        if win32pdh is None:
            return
        try:
            self._query = win32pdh.OpenQuery()
            self._engine = win32pdh.AddEnglishCounter(self._query, r"\GPU Engine(*)\Utilization Percentage")
            self._memory = win32pdh.AddEnglishCounter(self._query, r"\GPU Process Memory(*)\Dedicated Usage")
            win32pdh.CollectQueryData(self._query)  # the first collection only primes the rates
            self.available = True
        except Exception:  # noqa: BLE001 - counters missing (old Windows, no WDDM GPU)
            self.available = False

    def sample(self, app_pids: set[int]) -> list[GpuLoad]:
        if not self.available:
            return []
        try:
            win32pdh.CollectQueryData(self._query)
            engines = win32pdh.GetFormattedCounterArray(self._engine, win32pdh.PDH_FMT_DOUBLE)
            memory = win32pdh.GetFormattedCounterArray(self._memory, win32pdh.PDH_FMT_LARGE)
        except Exception:  # noqa: BLE001 - instances can vanish between collect and read
            return []
        loads = parse_engine_counters(engines, app_pids)
        by_adapter = {g.adapter: g for g in loads}
        for name, value in memory.items():
            m = _PROCESS_MEMORY.match(name)
            if m and int(m[1]) in app_pids and m[2] in by_adapter:
                by_adapter[m[2]].app_memory_mb += value / 2**20
        return loads

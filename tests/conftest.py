"""Test-wide setup.

TRACKXEN_COARSE_CLOCK=1 runs the tests with the clock Windows gives Python 3.12: time.monotonic() is
GetTickCount64() / 1000, whole milliseconds moving in steps of about 15.6 ms, and asyncio (which takes that
step as the clock's resolution) runs every timer due within one step at once. Tests that only pass with a
fine clock then show up on Linux too.
"""

import os
import time
import types

if os.environ.get("TRACKXEN_COARSE_CLOCK") == "1":
    _fine, _info = time.monotonic, time.get_clock_info

    def _coarse() -> float:
        ticks = int(_fine() * 64)  # 64 steps a second
        return int(ticks * 15.625) / 1000  # whole milliseconds, like GetTickCount64

    def _clock_info(name: str):
        info = _info(name)
        if name != "monotonic":
            return info
        return types.SimpleNamespace(adjustable=info.adjustable, implementation="GetTickCount64()",
                                     monotonic=True, resolution=0.015625)

    time.monotonic, time.get_clock_info = _coarse, _clock_info

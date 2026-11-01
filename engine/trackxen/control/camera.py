"""One camera's control connection: VISCA commands out, position (FreeD) in.

- Position comes from FreeD (every frame) with a short history, so the tracker can ask
  where the camera pointed when a video frame was captured. Without FreeD it polls the
  camera over VISCA instead.
- Movement requests are coalesced: only the newest wanted speed is sent, and only when
  it changes, so a slow camera never builds a queue of stale commands.
- VISCA round-trip times, errors and FreeD traffic are measured into the statistics.
"""

from __future__ import annotations

import asyncio
import bisect
import time
from collections import deque
from dataclasses import dataclass

from trackxen.devices import freed, visca


@dataclass(frozen=True)
class Pose:
    t: float  # monotonic time
    pan: float  # degrees
    tilt: float
    zoom: float  # raw lens value


@dataclass
class CameraLinkConfig:
    host: str
    visca_port: int = visca.VISCA_PORT
    freed_port: int | None = None
    use_header: bool = True
    units_per_degree: float = 16.0  # VISCA position units per degree (only used without FreeD)
    pan_sign: int = 1  # -1 if "pan right" makes FreeD pan go down on this camera
    tilt_sign: int = 1
    send_hz: float = 30.0


class CameraLink:
    def __init__(self, name: str, cfg: CameraLinkConfig, counters=None, log=None):
        self.name, self.cfg = name, cfg
        self.counters, self.log = counters, log
        self.visca = visca.ViscaClient(cfg.host, cfg.visca_port, timeout=0.5, use_header=cfg.use_header)
        self.history: deque[Pose] = deque(maxlen=600)
        self.last_freed = 0.0
        self.visca_ok = False
        self.last_error = ""
        self._want = (0, 0, 0)  # pan step, tilt step, zoom step (signed)
        self._sent = (None, None, None)
        self.last_motion = 0.0  # when we last started, changed or stopped a movement
        self._failures = 0
        self._freed_transport = None
        self._tasks: list[asyncio.Task] = []
        self._wake = asyncio.Event()
        self._motion = asyncio.Lock()  # one movement command at a time, in the order they were decided

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await self.visca.connect()
        if self.cfg.freed_port:
            self._freed_transport = await freed.listen(self.cfg.freed_port, self._on_freed,
                                                       on_bad=lambda _: self._count("freed_bad"))
        self._tasks = [asyncio.create_task(self._send_loop()), asyncio.create_task(self._poll_loop())]

    async def close(self) -> None:
        try:
            await self.stop_now()
        except Exception:  # noqa: BLE001 - closing anyway
            pass
        for task in self._tasks:
            task.cancel()
        if self._freed_transport:
            self._freed_transport.close()
        self.visca.close()

    # --- position --------------------------------------------------------------

    @property
    def freed_age(self) -> float:
        return time.monotonic() - self.last_freed if self.last_freed else float("inf")

    def pose_now(self) -> Pose | None:
        return self.history[-1] if self.history else None

    def pose_at(self, t: float) -> Pose | None:
        hist = list(self.history)
        if not hist:
            return None
        times = [p.t for p in hist]
        i = bisect.bisect_left(times, t)
        if i <= 0:
            return hist[0]
        if i >= len(hist):
            return hist[-1]
        a, b = hist[i - 1], hist[i]
        k = (t - a.t) / (b.t - a.t) if b.t > a.t else 0.0
        return Pose(t, a.pan + (b.pan - a.pan) * k, a.tilt + (b.tilt - a.tilt) * k, a.zoom + (b.zoom - a.zoom) * k)

    def _on_freed(self, pos: freed.FreeDPosition, addr, t: float) -> None:
        self.last_freed = t
        self.history.append(Pose(t, pos.pan, pos.tilt, float(pos.zoom)))
        self._count("freed_packets")

    async def _poll_loop(self) -> None:
        """Without fresh FreeD, ask the camera for its position over VISCA (~15 times a second)."""
        while True:
            await asyncio.sleep(1 / 15)
            if self.freed_age < 0.5:
                continue
            try:
                t0 = time.monotonic()
                pan, tilt = await self.visca.pan_tilt_position()
                zoom = await self.visca.zoom_position()
                self.history.append(Pose((t0 + time.monotonic()) / 2, pan / self.cfg.units_per_degree,
                                         tilt / self.cfg.units_per_degree, float(zoom)))
                self._count("position_polls")
            except (visca.ViscaError, OSError) as e:
                self._failed(e)
            except Exception as e:  # noqa: BLE001 - never let the position loop die silently
                self._unexpected("position poll", e)

    # --- movement --------------------------------------------------------------

    @property
    def commanding(self) -> bool:
        """Is a speed command of ours moving the camera right now?"""
        return self._want != (0, 0, 0)

    def request(self, pan_step: int, tilt_step: int, zoom_step: int = 0) -> None:
        """Wanted signed speed steps (0 = stop that axis); sent by the send loop."""
        want = (pan_step, tilt_step, zoom_step)
        if want != self._want:
            self._want = want
            self.last_motion = time.monotonic()
            self._wake.set()

    async def stop_now(self) -> None:
        """Stop immediately, bypassing the coalescing (panic button, watchdog, shutdown)."""
        self._want = (0, 0, 0)
        async with self._motion:
            self._want, self.last_motion = (0, 0, 0), time.monotonic()
            await self.visca.command(visca.pan_tilt_stop())
            await self.visca.command(visca.zoom_stop())
            self._sent = (0, 0, 0)

    async def recall_preset(self, number: int) -> None:
        async with self._motion:
            self._want = self._sent = (0, 0, 0)
            self.last_motion = time.monotonic()
            await self.visca.command(visca.preset_recall(number))

    async def goto(self, pan: float, tilt: float, zoom: float | None = None,
                   pan_speed: int = 12, tilt_speed: int | None = None) -> None:
        """Absolute move (degrees, raw zoom). Needs units_per_degree calibrated for real cameras.

        Afterwards the speed loop sends nothing until the next request(), so the move isn't
        cancelled; the motion lock keeps a speed command already on its way from landing after it.
        """
        async with self._motion:
            self._want = self._sent = (0, 0, 0)
            self.last_motion = time.monotonic()
            u = self.cfg.units_per_degree
            tilt_speed = min(23, pan_speed if tilt_speed is None else tilt_speed)
            await self.visca.command(visca.pan_tilt_absolute(round(pan * u * self.cfg.pan_sign),
                                                             round(tilt * u * self.cfg.tilt_sign), pan_speed, tilt_speed))
            if zoom is not None:
                await self.visca.command(visca.zoom_direct(round(zoom)))

    async def _send_loop(self) -> None:
        period = 1.0 / self.cfg.send_hz
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=period)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                async with self._motion:  # read the wish only once absolute moves are out of the way
                    want = self._want
                    pan_step, tilt_step, zoom_step = want
                    if (pan_step, tilt_step) != self._sent[:2]:
                        await self._timed(self._drive_message(pan_step, tilt_step))
                    if zoom_step != self._sent[2]:
                        msg = (visca.zoom_stop() if zoom_step == 0 else
                               visca.zoom_tele(abs(zoom_step) - 1) if zoom_step > 0 else visca.zoom_wide(abs(zoom_step) - 1))
                        await self._timed(msg)
                    self._sent = want
            except (visca.ViscaError, OSError) as e:
                self._failed(e)
            except Exception as e:  # noqa: BLE001 - never let the command loop die silently
                self._unexpected("command send", e)
            await asyncio.sleep(period)  # never faster than send_hz

    def _unexpected(self, where: str, error: Exception) -> None:
        self._count("internal_errors")
        if self.log:
            self.log.event("camera", f"unexpected error in {where}: {error!r}", level="error", source=self.name)

    def _drive_message(self, pan_step: int, tilt_step: int) -> bytes:
        pan_step *= self.cfg.pan_sign
        tilt_step *= self.cfg.tilt_sign
        pan_dir = visca.PanDir.RIGHT if pan_step > 0 else visca.PanDir.LEFT if pan_step < 0 else visca.PanDir.STOP
        tilt_dir = visca.TiltDir.UP if tilt_step > 0 else visca.TiltDir.DOWN if tilt_step < 0 else visca.TiltDir.STOP
        return visca.pan_tilt_drive(max(1, abs(pan_step)), max(1, abs(tilt_step)), pan_dir, tilt_dir)

    async def _timed(self, msg: bytes) -> None:
        t0 = time.perf_counter()
        await self.visca.command(msg)
        if self.counters:
            self.counters.observe(f"{self.name}.visca_rtt_ms", (time.perf_counter() - t0) * 1000)
        self._count("visca_commands")
        if not self.visca_ok and self.log:
            self.log.event("camera", "camera answering VISCA", source=self.name)
        self.visca_ok, self._failures = True, 0

    def _failed(self, error: Exception) -> None:
        self._count("visca_errors")
        self._failures += 1
        self.last_error = str(error)
        if self._failures >= 3 and self.visca_ok:
            self.visca_ok = False
            if self.log:
                self.log.event("camera", f"camera not answering VISCA: {error}", level="error", source=self.name)
        if self._failures % 5 == 0:  # keep trying with a fresh socket (camera rebooted, network blip)
            asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        old = self.visca
        self.visca = visca.ViscaClient(self.cfg.host, self.cfg.visca_port, timeout=0.5, use_header=self.cfg.use_header)
        old.close()
        try:
            await self.visca.connect()
            self._sent = (None, None, None)  # resend the wanted speeds on the new connection
        except OSError as e:
            self.last_error = str(e)

    def _count(self, what: str) -> None:
        if self.counters:
            self.counters.add(f"{self.name}.{what}")

"""One camera's control connection: VISCA commands out, position (FreeD) in.

- Position comes from FreeD (every frame) with a short history, so the tracker can ask
  where the camera pointed when a video frame was captured. Without FreeD (PTZOptics G2) it
  polls the camera over VISCA instead, `poll_hz` times a second.
- Speeds are kept within the camera's own range (a PTZOptics tilts at most at step 0x14).
- Movement requests are coalesced: only the newest wanted speed is sent, and only when
  it changes, so a slow camera never builds a queue of stale commands.
- VISCA round-trip times, errors and FreeD traffic are measured into the statistics.
- A movement command that fails may still have reached the camera, so the wanted speeds are
  sent again; a position older than `POSE_MAX_AGE` is not used.
"""

from __future__ import annotations

import asyncio
import bisect
import time
from collections import deque
from dataclasses import dataclass

from trackxen.devices import freed, udp, visca

POSE_MAX_AGE = 2.0  # s: an older position is not the camera's position now (FreeD and inquiries both stopped)
CLOSE_STOP_TRIES = 3  # close() sends the stop up to this many times until the camera acknowledges it


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
    transport: str = "sony"  # VISCA: "sony" (header, UDP), "udp" (plain) or "tcp" (plain), see devices/visca.py
    units_per_degree: float = 16.0  # VISCA position units per degree, pan (only used without FreeD)
    tilt_units_per_degree: float | None = None  # ... tilt, if it differs
    # -1 if the camera's VISCA pan (drive, absolute moves and position replies) runs against its picture and
    # FreeD, i.e. if "pan right" makes FreeD pan go down on this camera
    pan_sign: int = 1
    tilt_sign: int = 1
    pan_speed_max: int = visca.PAN_SPEED_MAX  # the camera's fastest VISCA speed step
    tilt_speed_max: int = visca.TILT_SPEED_MAX
    send_hz: float = 30.0
    poll_hz: float = 20.0  # position inquiries per second without FreeD

    @property
    def tilt_units(self) -> float:
        return self.tilt_units_per_degree or self.units_per_degree


class CameraLink:
    def __init__(self, name: str, cfg: CameraLinkConfig, counters=None, log=None):
        self.name, self.cfg = name, cfg
        self.counters, self.log = counters, log
        self.visca = visca.open_client(cfg.host, cfg.visca_port, cfg.transport, timeout=0.5)
        self.history: deque[Pose] = deque(maxlen=600)
        self.last_freed = 0.0
        self.visca_ok = False
        self.last_error = ""
        self._want = (0, 0, 0)  # pan step, tilt step, zoom step (signed)
        self._sent = (None, None, None)
        self.last_motion = 0.0  # when we last started, changed or stopped a movement
        self._failures = 0
        self._freed_transport = None
        self._freed_ip: str | None = None  # the camera's address: FreeD from anywhere else is ignored
        self._foreign: set[str] = set()  # other senders of FreeD to this port (logged once each)
        self._tasks: list[asyncio.Task] = []
        self._reconnecting: asyncio.Task | None = None
        self._closed = False
        self._wake = asyncio.Event()
        self._motion = asyncio.Lock()  # one movement command at a time, in the order they were decided

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Connect and start the send and position loops. The loops start even when connecting fails
        (camera off, name not found, FreeD port taken): they keep reconnecting, and positions come from
        VISCA without FreeD. The first error is raised afterwards, for the log.

        Called again after a failed start (the app tries such a link again later), it only opens what
        is still missing, the FreeD port, and raises while VISCA doesn't answer yet. It never starts
        the loops twice or closes the VISCA connection the loops have made meanwhile."""
        error: Exception | None = None
        if not self._tasks:
            try:
                await self.visca.connect()
            except (OSError, ValueError) as e:  # (ValueError: UnicodeError for a typo such as '192.168.1..60')
                error, self.last_error = e, str(e) or type(e).__name__
        elif not self.visca_ok:  # the loops reconnect by themselves; it just doesn't answer yet
            error = ConnectionError(self.last_error or "camera not answering VISCA")
        if self.cfg.freed_port and not self._freed_transport:
            remote = getattr(self.visca, "_remote", None)
            try:
                self._freed_ip = remote[0] if remote else await udp.resolve_async(self.cfg.host)
            except (OSError, ValueError):  # (UnicodeError for a typo such as '192.168.1..60')
                self._freed_ip = None  # unknown: FreeD from any sender
            try:
                self._freed_transport = await freed.listen(self.cfg.freed_port, self._on_freed,
                                                           on_bad=lambda _: self._count("freed_bad"))
            except OSError as e:
                error, self.last_error = error or e, str(e)
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._send_loop()), asyncio.create_task(self._poll_loop())]
        if error:
            raise error

    async def close(self) -> None:
        self._closed = True
        for _ in range(CLOSE_STOP_TRIES):  # a lost or refused stop is sent again: nothing stops this camera after this
            try:
                await self.stop_now()
                break
            except Exception:  # noqa: BLE001 - closing anyway
                for task in self._tasks:  # (it goes out from here at once, not after position inquiries)
                    task.cancel()
        if self._reconnecting:
            self._reconnecting.cancel()
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
        """The newest position; None if there is none, or it is too old to be where the camera is now."""
        if not self.history or time.monotonic() - self.history[-1].t > POSE_MAX_AGE:
            return None
        return self.history[-1]

    def pose_at(self, t: float) -> Pose | None:
        hist = list(self.history)
        if not hist or t - hist[-1].t > POSE_MAX_AGE:
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
        if self._freed_ip and addr[0] != self._freed_ip:  # another camera sending to this port by mistake
            self._count("freed_foreign")
            if addr[0] not in self._foreign:
                self._foreign.add(addr[0])
                if self.log:
                    self.log.event("camera", f"FreeD from {addr[0]} ignored: this camera is {self._freed_ip}",
                                   level="warning", source=self.name)
            return
        self.last_freed = t
        self.history.append(Pose(t, pos.pan, pos.tilt, float(pos.zoom)))
        self._count("freed_packets")

    async def _poll_loop(self) -> None:
        """Without fresh FreeD, ask the camera for its position over VISCA (`poll_hz` times a second)."""
        while True:
            await asyncio.sleep(1 / self.cfg.poll_hz)
            if self.freed_age < 0.5:
                continue
            try:
                t0 = time.monotonic()
                pan, tilt = await self.visca.pan_tilt_position()
                zoom = await self.visca.zoom_position()
                self._answered()
                if self.last_freed > t0:
                    continue  # FreeD came back meanwhile: newer (and the history stays in time order)
                # (the same frame as absolute moves, so the same signs)
                self.history.append(Pose((t0 + time.monotonic()) / 2,
                                         pan / self.cfg.units_per_degree * self.cfg.pan_sign,
                                         tilt / self.cfg.tilt_units * self.cfg.tilt_sign, float(zoom)))
                self._count("position_polls")
            except (visca.ViscaError, OSError, ValueError) as e:  # (ValueError: a garbled reply)
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
            try:
                await self.visca.command(visca.pan_tilt_stop())
                await self.visca.command(visca.zoom_stop())
            except BaseException:
                self._unsure()  # the send loop keeps sending the stop
                raise
            self._sent = (0, 0, 0)

    async def recall_preset(self, number: int) -> None:
        async with self._motion:
            self._want = self._sent = (0, 0, 0)
            self.last_motion = time.monotonic()
            try:
                await self.visca.command(visca.preset_recall(number))
            except BaseException:
                self._unsure()  # the camera may still be driving: the stop goes out
                raise

    async def goto(self, pan: float, tilt: float, zoom: float | None = None,
                   pan_speed: int = 12, tilt_speed: int | None = None) -> None:
        """Absolute move (degrees, raw zoom). Needs units_per_degree calibrated for real cameras.

        Afterwards the speed loop sends nothing until the next request(), so the move isn't
        cancelled; the motion lock keeps a speed command already on its way from landing after it.
        """
        async with self._motion:
            self._want = self._sent = (0, 0, 0)
            self.last_motion = time.monotonic()
            pan_speed = min(self.cfg.pan_speed_max, pan_speed)
            tilt_speed = min(self.cfg.tilt_speed_max, pan_speed if tilt_speed is None else tilt_speed)
            try:
                await self.visca.command(visca.pan_tilt_absolute(round(pan * self.cfg.units_per_degree * self.cfg.pan_sign),
                                                                 round(tilt * self.cfg.tilt_units * self.cfg.tilt_sign),
                                                                 pan_speed, tilt_speed))
            except BaseException:
                self._unsure()  # the camera may still be driving: the stop goes out
                raise
            if zoom is not None:
                try:
                    await self.visca.command(visca.zoom_direct(round(zoom)))
                except BaseException:
                    self._unsure(zoom_only=True)  # the zoom may still be running (the pan/tilt move goes on)
                    raise

    def _unsure(self, zoom_only: bool = False) -> None:
        """A movement command failed: the camera may or may not have done it, so the send loop sends the
        wanted speeds again (a stop, after a failed absolute move)."""
        self._sent = (*self._sent[:2], None) if zoom_only else (None, None, None)
        self._wake.set()

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
                self._unsure()
                self._failed(e)
            except Exception as e:  # noqa: BLE001 - never let the command loop die silently
                self._unsure()
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
        return visca.pan_tilt_drive(max(1, min(abs(pan_step), self.cfg.pan_speed_max)),
                                    max(1, min(abs(tilt_step), self.cfg.tilt_speed_max)), pan_dir, tilt_dir)

    async def _timed(self, msg: bytes) -> None:
        t0 = time.perf_counter()
        await self.visca.command(msg)
        if self.counters:
            self.counters.observe(f"{self.name}.visca_rtt_ms", (time.perf_counter() - t0) * 1000)
        self._count("visca_commands")
        self._answered()

    def _answered(self) -> None:
        """The camera answered (a command or a position inquiry): failures count from zero again."""
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
        if self._failures % 5 == 0 and not self._reconnecting and not self._closed:
            # keep trying with a fresh socket (camera rebooted, network blip), one attempt at a time
            self._reconnecting = asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        try:
            old = self.visca
            new = self.visca = visca.open_client(self.cfg.host, self.cfg.visca_port, self.cfg.transport, timeout=0.5)
            old.close()  # first: a camera may take only one TCP connection
            try:
                await new.connect()
            except (OSError, ValueError) as e:  # (ValueError: a host name with a typo)
                self.last_error = str(e) or type(e).__name__
                return
            if self._closed or self.visca is not new:  # closed meanwhile: leave no connection open
                new.close()
                return
            async with self._motion:
                if self._want != (0, 0, 0):  # (a stop would cancel an absolute move under way)
                    self._unsure()  # send the wanted speeds again on the new connection
        finally:
            self._reconnecting = None

    def _count(self, what: str) -> None:
        if self.counters:
            self.counters.add(f"{self.name}.{what}")

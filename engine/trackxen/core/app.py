"""The core service: runs the vision worker, the camera sessions, safety watchdogs and statistics."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from pathlib import Path

from trackxen.control.camera import CameraLink, CameraLinkConfig
from trackxen.core.config import AppConfig, CameraConfig, DetectionSettings, data_dir, engine_cache_dir, models_dir
from trackxen.core.faces import FaceManager
from trackxen.core.gpu_libs import GpuLibraries
from trackxen.core.live_inputs import LiveInputs
from trackxen.core.performance import PRESETS, Preset
from trackxen.core.session import CameraSession
from trackxen.core.stats import Counters, Limits, StatsCollector, StatsLog, SystemSampler
from trackxen.core.tracking_settings import View
from trackxen.rules.audio import AudioFollowConfig
from trackxen.vision.sources import SourceConfig
from trackxen.vision.worker import WorkerConfig, start_worker

VIDEO_TIMEOUT = 0.5  # seconds without detections before the watchdog stops a tracking camera
CONTROL_HZ = 50


LIVE_CAMERA_FIELDS = {"slots", "active_slot", "preset_links", "faces", "vmix_input"}  # edited through commands


def _camera_setup(config: AppConfig) -> list[dict]:
    """The camera settings that need the camera connections restarted when they change."""
    return [c.model_dump(exclude=LIVE_CAMERA_FIELDS) for c in config.cameras]


def source_config(cam: CameraConfig, low_bandwidth: bool = False) -> SourceConfig:
    """Where the vision worker takes this camera's picture from."""
    if cam.video == "capture":
        return SourceConfig(cam.id, "capture", str(cam.capture_device))
    if cam.video == "stream":
        return SourceConfig(cam.id, "stream", cam.video_url, transport=cam.stream_transport)
    return SourceConfig(cam.id, "ndi", cam.ndi, cam.low_bandwidth or low_bandwidth)


def _resolve_host(host_name: str) -> str:
    import socket

    for name in (f"{host_name}.local", host_name):
        try:
            return socket.getaddrinfo(name, None, socket.AF_INET)[0][4][0]
        except OSError:
            continue
    raise OSError(f"can't resolve {host_name}")


class App:
    def __init__(self, config: AppConfig, data: Path | None = None, models: Path | None = None,
                 config_path: Path | None = None):
        self.config = config
        self.config_path = config_path  # where settings are saved (None: not saved, e.g. --sim)
        self.data = data or data_dir()
        self.models = models or models_dir()
        self.hwtest: dict = {"running": False, "lines": [], "report": None}
        self.ndi_baseline: dict[str, tuple[int, int]] = {}  # Statistics "Reset": (dropped, total) at reset
        self.log = StatsLog(self.data / "logs", keep_days=config.log_keep_days)
        self.counters = Counters()
        self.sampler = SystemSampler(app_pids={os.getpid()})
        self.limits = Limits()
        self.collector = StatsCollector(self.log, self.counters, self.limits, self.sampler)
        self.sessions: dict[str, CameraSession] = {}
        self.faces = FaceManager(self)  # registered faces, only on this PC
        self.inputs = LiveInputs(self)  # vMix tally and the mixer's microphones
        self.previews: dict[str, tuple[int, bytes]] = {}  # camera -> (sequence, jpeg)
        self.preview_seq = 0
        self.worker = None
        self.worker_pid: int | None = None
        self.worker_started = 0.0
        self.engine = ""
        self.engine_building = False  # TensorRT being built for this GPU (first start: minutes)
        self.engine_building_since = 0.0
        # NVIDIA's libraries: the installed app downloads them on its first start (TRACKXEN_GPU_LIBS:
        # in development too, for testing); in development they come with the Python packages
        self.gpu_libs: GpuLibraries | None = None
        if getattr(sys, "frozen", False) or os.environ.get("TRACKXEN_GPU_LIBS"):
            self.gpu_libs = GpuLibraries(log=lambda level, message: self.log.event("gpu", message, level=level))
        self.listeners: set = set()  # callables notified on new previews (web sockets)
        self._tasks: list[asyncio.Task] = []
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop.set_exception_handler(self._on_loop_error)
        self.log.event("app", f"started with {len(self.config.cameras)} camera(s)")
        await self._start_cameras()
        if self.gpu_libs and self.gpu_libs.state != "not_needed":
            if await asyncio.to_thread(self.gpu_libs.check):  # (hashes each file once, the first time)
                self._use_gpu_libs()
            else:  # first start: download them; detection runs on the processor meanwhile
                self.gpu_libs.start(on_ready=lambda: self._loop.call_soon_threadsafe(self._gpu_libs_ready))
        self._start_worker()
        self.inputs.start()
        self._tasks = [asyncio.create_task(self._control_loop()), asyncio.create_task(self._watchdog()),
                       asyncio.create_task(self.collector.run())]

    async def _start_cameras(self) -> None:
        for cam in self.config.cameras:
            link = CameraLink(cam.id, CameraLinkConfig(
                host=cam.host, visca_port=cam.visca_port, freed_port=cam.freed_port, transport=cam.visca_transport,
                units_per_degree=cam.units_per_degree, tilt_units_per_degree=cam.tilt_units_per_degree,
                pan_sign=cam.pan_sign, tilt_sign=cam.tilt_sign,
                # the speed tables have one entry per VISCA speed step the camera has
                pan_speed_max=len(cam.pan_speeds), tilt_speed_max=len(cam.tilt_speeds)),
                counters=self.counters, log=self.log)
            if cam.host:
                try:
                    await link.start()
                except OSError as e:
                    self.log.event("camera", f"can't open camera connection: {e}", level="error", source=cam.id)
            self.sessions[cam.id] = CameraSession(cam, link, self.counters, self.log)
            self.sessions[cam.id].face_names = self.faces.names

    async def apply_config(self, config: AppConfig, whole: bool = False) -> None:
        """Save new settings and restart the cameras and vision with them (cameras are stopped first).

        App Settings, faces, preset links, vMix and audio settings are edited live through commands,
        so the running ones are kept (a settings page may hold an older copy of them) unless `whole`
        (loading a show file) replaces everything.
        """
        if not whole:
            running = {c.id: c for c in self.config.cameras}
            for cam in config.cameras:
                if cam.id in running:
                    old = running[cam.id]
                    cam.slots, cam.active_slot, cam.preset_links = old.slots, old.active_slot, old.preset_links
                    cam.faces, cam.vmix_input = old.faces, old.vmix_input
            config.vmix, config.mixer, config.audio = self.config.vmix, self.config.mixer, self.config.audio
        else:  # a show file from elsewhere: faces it names that this PC doesn't have are left out
            for cam in config.cameras:
                cam.faces = [f for f in cam.faces if f.id in self.faces.store.faces]
        same_cameras = not whole and _camera_setup(config) == _camera_setup(self.config)
        if same_cameras:  # e.g. only the performance mode changed: the cameras keep running and tracking
            config.cameras = self.config.cameras
        if self.config_path:
            config.save(self.config_path)
        self._stop_worker()
        if not same_cameras:
            for s in self.sessions.values():
                await s.link.close()
            self.sessions.clear()
        self.config = config
        self.limits.expected_fps.clear()
        if not same_cameras:
            await self._start_cameras()
        self._start_worker()
        if whole:
            self.inputs.start()
        self.inputs.on_tally()
        self.log.event("settings", "show file loaded" if whole else
                       "settings saved and applied" + ("" if not same_cameras else " (vision restarted)"))

    def _on_loop_error(self, loop, context: dict) -> None:
        """Unexpected errors anywhere in the app go to the text log (and the console)."""
        error = context.get("exception")
        self.log.event("app", f"unexpected error: {context.get('message', '')} {error!r}", level="error")
        loop.default_exception_handler(context)

    def _use_gpu_libs(self) -> None:
        from trackxen.vision.runtime import use_gpu_libraries

        use_gpu_libraries(self.gpu_libs.dir)  # here too: the hardware test runs in this process

    def _gpu_libs_ready(self) -> None:
        """The download finished: the vision worker starts again, now on the GPU."""
        self._use_gpu_libs()
        if self._stopping:
            return
        self.log.event("vision", "NVIDIA libraries downloaded: restarting the vision worker on the GPU")
        self._stop_worker()
        self._start_worker()

    async def stop(self) -> None:
        self._stopping = True
        if self.gpu_libs:
            self.gpu_libs.stop()
        for task in self._tasks:
            task.cancel()
        for s in self.sessions.values():
            await s.link.close()
        self._stop_worker()
        self.inputs.stop()
        self.log.event("app", "stopped")

    def active_preset(self) -> Preset:
        mode = self.config.performance_mode
        name = mode if mode in ("light", "hyper") else self.config.auto_preset
        return PRESETS.get(name, PRESETS["normal"])

    def _start_worker(self) -> None:
        preset = self.active_preset()
        model = self.config.model_override or f"{preset.model}-{preset.imgsz}"
        cfg = WorkerConfig(
            sources=[source_config(c, preset.low_bandwidth) for c in self.config.cameras
                     if c.video == "capture" or (c.video == "stream" and c.video_url) or (c.video == "ndi" and c.ndi)],
            model_dir=str(self.models), cache_dir=str(engine_cache_dir()), model_name=model,
            engines=tuple(self.config.engines),
            gpu_libs=str(self.gpu_libs.dir) if self.gpu_libs and self.gpu_libs.ready else "",
            preview_fps=preset.preview_fps, max_detect_fps=preset.detect_fps,
            tracker=self.config.detection.tracker_args())
        self.worker = start_worker(cfg)
        self.worker_started = time.monotonic()
        self._reader = threading.Thread(target=self._read_results, args=(self.worker[2],), daemon=True)
        self._reader.start()
        self.faces.worker_started()
        self.log.event("vision", f"vision worker starting: {self.config.performance_mode} mode, "
                                 f"preset {preset.name} ({model})")

    def _stop_worker(self) -> None:
        if not self.worker:
            return
        proc, commands, _ = self.worker
        try:
            commands.put({"type": "stop"})
            proc.join(timeout=3)
        finally:
            if proc.is_alive():
                proc.terminate()
        self.sampler.app_pids.discard(self.worker_pid)
        self.worker = None

    def _read_results(self, results) -> None:
        """Thread: hand worker messages to the event loop."""
        while not self._stopping:
            try:
                msg = results.get(timeout=0.5)
            except queue.Empty:
                if not self.worker or results is not self.worker[2]:
                    return
                continue
            except (EOFError, OSError):
                return
            if self._loop:
                self._loop.call_soon_threadsafe(self._on_message, msg)

    # --- worker messages -----------------------------------------------------------------

    def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        session = self.sessions.get(msg.get("camera", ""))
        if kind == "tracks" and session:
            session.video_resumed()
            session.on_tracks(msg)
        elif kind == "preview":
            self.preview_seq += 1
            self.previews[msg["camera"]] = (self.preview_seq, msg["jpeg"])
            for notify in list(self.listeners):
                notify()
        elif kind == "stats" and session:
            session.source_stats = msg
            source_fps = msg.get("fps", 0.0)
            self.counters.set(f"{msg['camera']}.source_fps", source_fps)
            if source_fps > 0:  # the mode's detection limit (Light: 15/s) is not a problem
                limit = self.active_preset().detect_fps
                self.limits.expected_fps[msg["camera"]] = min(source_fps, limit) if limit > 0 else source_fps
            self.counters.set(f"{msg['camera']}.ndi_dropped_total", msg.get("ndi_dropped", 0))
            self.counters.set(f"{msg['camera']}.ndi_frames_total", msg.get("ndi_total", 0))
            if msg.get("decode_ms") is not None:  # a network stream (RTSP): decoding time per frame
                self.counters.set(f"{msg['camera']}.stream_decode_ms", msg["decode_ms"])
        elif kind == "ready":
            self.worker_pid = msg["pid"]
            self.sampler.app_pids.add(msg["pid"])
            self.engine = msg["engine"]
            self.engine_building = bool(msg.get("building"))
            self.engine_building_since = time.monotonic()
            self.log.event("vision", f"vision worker ready (engine {msg['engine']})"
                                     + ("; building the TensorRT engine for this GPU" if self.engine_building else ""))
        elif kind == "engine":
            self.engine, self.engine_building = msg["engine"], False
            self.log.event("vision", msg["message"])
        elif kind == "error":
            self.log.event("vision", msg["message"], level="error", source=msg.get("camera", ""))
        else:
            self.faces.on_message(msg)

    # --- loops -----------------------------------------------------------------------------

    async def _control_loop(self) -> None:
        period = 1.0 / CONTROL_HZ
        while True:
            start, t0 = time.monotonic(), time.perf_counter()  # (monotonic ticks every 15.6 ms on Windows)
            for s in self.sessions.values():
                s.tick(start)
            took = time.perf_counter() - t0
            self.counters.observe("app.control_loop_ms", took * 1000)
            await asyncio.sleep(max(0.0, period - took))

    async def _watchdog(self) -> None:
        """Safety: stop cameras when detections stop; restart a dead vision worker."""
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            warming_up = now - self.worker_started < 20  # model loading
            for s in self.sessions.values():
                if s.tracking and not s.no_video and now - s.last_tracks_at > VIDEO_TIMEOUT and not warming_up:
                    s.video_stopped()
                    try:
                        await s.link.stop_now()
                    except Exception as e:  # noqa: BLE001 - camera unreachable: nothing else to do
                        self.log.event("camera", f"stop failed: {e}", level="error", source=s.cfg.id)
            if self.worker and not self.worker[0].is_alive() and not self._stopping:
                self.log.event("vision", "vision worker stopped unexpectedly; restarting", level="error")
                for s in self.sessions.values():
                    if s.tracking:
                        s.video_stopped()
                        await s.link.stop_now()
                self.sampler.app_pids.discard(self.worker_pid)
                self.worker = None
                await asyncio.sleep(2.0)
                self._start_worker()

    # --- operator commands -------------------------------------------------------------------

    async def command(self, cmd: dict) -> dict:
        kind = cmd.get("type")
        session = self.sessions.get(cmd.get("camera", ""))
        if kind == "stop_all":
            for s in self.sessions.values():
                s.set_tracking(False, "STOP ALL")
                s.move = None
                try:
                    await s.link.stop_now()
                except Exception as e:  # noqa: BLE001 - one unreachable camera must not block the others
                    self.log.event("camera", f"stop failed: {e}", level="error", source=s.cfg.id)
            self.log.event("operator", "STOP ALL pressed", level="warning")
            return {"ok": True}
        if kind == "gpu_libs_retry":  # "Download again" after a failed download of NVIDIA's libraries
            if self.gpu_libs:
                self.gpu_libs.retry()
            return {"ok": True}
        if kind == "connections":  # vMix and mixer addresses: only those links restart
            for key in ("vmix", "mixer"):
                if key in cmd:
                    current = getattr(self.config, key)
                    setattr(self.config, key, type(current).model_validate({**current.model_dump(), **cmd[key]}))
            if self.config_path:
                self.config.save(self.config_path)
            self.inputs.start()
            self.inputs.on_tally()
            self.log.event("settings", f"connections: vMix {self.config.vmix.host or 'off'}, "
                                       f"mixer {self.config.mixer.host or 'off'}")
            return {"ok": True}
        if kind == "audio_settings":  # audio-follow settings and mic mappings (validated, saved)
            data = {**self.config.audio.model_dump(), **cmd["settings"]}
            try:
                self.config.audio = AudioFollowConfig.model_validate(data)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            if self.config_path:
                self.config.save(self.config_path)
            return {"ok": True, "audio": self.config.audio.model_dump()}
        if kind == "detection":  # Advanced mode: certainty thresholds
            values = {**self.config.detection.model_dump(), **cmd.get("settings", {})}
            self.config.detection = DetectionSettings.model_validate(values)  # validates the ranges
            if self.worker:
                self.worker[1].put({"type": "tracker", "settings": self.config.detection.tracker_args()})
            if self.config_path:
                self.config.save(self.config_path)
            self.log.event("settings", f"detection thresholds changed: {self.config.detection.model_dump()}")
            return {"ok": True, "detection": self.config.detection.model_dump()}
        if session is None:
            return {"ok": False, "error": "unknown camera"}
        try:
            reply = self._camera_command(session, kind, cmd)
        except (ValueError, KeyError, IndexError, TypeError) as e:  # bad values (pydantic errors are ValueErrors)
            reply = {"ok": False, "error": str(e)}
        reply["rev"] = session.settings_rev  # lets a page tell its own settings changes from others'
        return reply

    def _camera_command(self, session: CameraSession, kind: str, cmd: dict) -> dict:
        index = int(cmd.get("index", 0))
        if kind == "on_air":  # the manual on-air switch: true, false, or null to follow vMix
            session.set_on_air(None if cmd.get("on") is None else bool(cmd["on"]))
            return {"ok": True}
        if kind == "audio_follow":
            session.audio_follow = bool(cmd["on"])
            return {"ok": True}
        if kind == "vmix_input":  # which vMix input shows this camera (key or number; "" = none)
            session.cfg.vmix_input = str(cmd.get("input", ""))
            if self.config_path:
                self.config.save(self.config_path)
            self.inputs.on_tally()
            return {"ok": True}
        if kind.startswith("face_"):
            return self.faces.command(session, kind, cmd)
        if kind == "select":
            target, reason = session.select_at(float(cmd["x"]), float(cmd["y"]))
            return {"ok": True, "target": target, "message": reason}
        if kind == "clear_target":
            session.select_id(None)
        elif kind == "tracking":
            session.set_tracking(bool(cmd["on"]))
        elif kind == "manual":
            session.manual(int(cmd.get("pan", 0)), int(cmd.get("tilt", 0)), int(cmd.get("zoom", 0)))
        elif kind == "preset":
            session.recall_preset(int(cmd["number"]))
        elif kind == "preset_link":  # Camera Preset Link row: auto_tracking "", "on" or "off"; slot 0..4
            session.set_preset_link(int(cmd["number"]), str(cmd.get("auto_tracking", "")), int(cmd.get("slot", 0)))
        elif kind == "preset_links_reset":
            session.cfg.preset_links = {}
            session.settings_rev += 1
        elif kind == "slot":  # App Settings 1..5 (index 0..4)
            session.set_slot(index)
        elif kind == "slot_settings":  # partial App Settings change, live; kept after "save"
            slot = session.update_slot(cmd["settings"], cmd.get("slot"))  # (may change other values too)
            return {"ok": True, "problems": slot.problems(session.lens), "settings": slot.model_dump()}
        elif kind == "framing":  # composition values (older pages send these directly)
            keys = ("anchor_x", "anchor_y", "dead_zone_x", "dead_zone_y", "display_size", "auto_zoom", "target_height")
            session.update_slot({"composition": {k: cmd[k] for k in keys if k in cmd}})
        elif kind == "area":  # place (rect on the picture, 0..1) and/or switch an area on/off
            session.set_area(str(cmd["kind"]), index, cmd.get("rect"), cmd.get("enabled"))
            return {"ok": True, "problems": session.slot.problems(session.lens)}
        elif kind == "capture":  # "initial", a shooting range edge (left/right/...) or an area's view
            session.capture(str(cmd["what"]), index)
            return {"ok": True, "problems": session.slot.problems(session.lens)}
        elif kind == "view":  # "initial", a range edge, or an area kind with an index
            session.show(str(cmd["what"]), index)
        elif kind == "goto":  # pan/tilt/zoom sliders of the settings pages
            pose = session.require_pose()
            session.go_to_manually(View(set=True, pan=float(cmd.get("pan", pose.pan)), tilt=float(cmd.get("tilt", pose.tilt)),
                                        zoom=float(cmd.get("zoom", pose.zoom))), "manual position")
        elif kind == "click_center":
            session.click_center(float(cmd["x"]), float(cmd["y"]))
        elif kind == "initialize":  # "slot" (the selected App Settings) or "all"
            session.initialize("all" if cmd.get("scope") == "all" else "slot")
        elif kind == "save":  # Canon's Save: changes made on the tracking screen are kept
            if self.config_path:
                self.config.save(self.config_path)
            self.log.event("settings", "tracking settings saved", source=session.cfg.id)
        else:
            return {"ok": False, "error": f"unknown command {kind}"}
        return {"ok": True}

    def slots(self, camera: str) -> dict:
        """The camera's App Settings and preset links, for the settings pages."""
        session = self.sessions[camera]
        cfg = session.cfg
        return {"active": cfg.active_slot, "rev": session.settings_rev,
                "slots": [s.model_dump() for s in cfg.slots],
                "preset_links": {str(k): v.model_dump() for k, v in cfg.preset_links.items()},
                "problems": [s.problems(session.lens) for s in cfg.slots]}

    def status(self) -> dict:
        cameras = []
        for s in self.sessions.values():
            st = s.status()
            src = st["source"] or {}
            dropped0, total0 = self.ndi_baseline.get(s.cfg.id, (0, 0))
            st["ndi_dropped"] = max(0, src.get("ndi_dropped", 0) - dropped0)  # since the last Reset
            st["ndi_total"] = max(0, src.get("ndi_total", 0) - total0)
            st.update(self.faces.camera_status(s.cfg.id))
            cameras.append(st)
        return {
            "engine": self.engine, "worker_alive": bool(self.worker and self.worker[0].is_alive()),
            "cameras": cameras,
            "stats": self.collector.latest,
            "problems": [p.__dict__ for p in self.collector.problems] + (self.gpu_libs.problems() if self.gpu_libs else []),
            "gpu_libs": self.gpu_libs.status() if self.gpu_libs else {"state": "not_needed"},
            "log_file": str(self.log.text_path),
            "hwtest_running": self.hwtest["running"],
            **self.inputs.status(),
            "audio_enabled": self.config.audio.enabled,
            "engine_building": self.engine_building,
            "engine_building_s": round(time.monotonic() - self.engine_building_since) if self.engine_building else 0,
            "performance": {"mode": self.config.performance_mode, "preset": self.active_preset().name,
                            "model": self.config.model_override or self.active_preset().model,
                            "imgsz": self.active_preset().imgsz},
        }

    def reset_statistics(self) -> None:
        """Statistics page "Reset", like vMix: zero the counts that add up over time."""
        for s in self.sessions.values():
            src = s.source_stats or {}
            self.ndi_baseline[s.cfg.id] = (src.get("ndi_dropped", 0), src.get("ndi_total", 0))
            s.reset_counts()
        self.log.event("statistics", "statistics reset")

    async def discover(self) -> dict:
        """NDI sources (with BirdDog cameras behind them) and MR18 / X Air mixers on the network."""
        from trackxen.devices.birddog_rest import BirdDogRest
        from trackxen.devices.ndi_in import find_sources
        from trackxen.integrations import xair

        sources = await asyncio.to_thread(find_sources, 3.0)
        found = []
        for src in sources:
            entry = {"ndi": src.name, "host": "", "birddog": None}
            try:
                entry["host"] = await asyncio.to_thread(_resolve_host, src.host_name)
                async with BirdDogRest(entry["host"], timeout=1.0) as rest:
                    entry["birddog"] = await rest.about()
            except Exception:  # noqa: BLE001 - not resolvable or not a BirdDog: still list the source
                pass
            found.append(entry)
        mixers = [m.__dict__ for m in await xair.discover(timeout=1.5)]
        return {"ndi": found, "mixers": mixers}

    async def test_camera(self, cam: CameraConfig) -> dict:
        """Settings -> Cameras "Test camera": does it answer VISCA (and where does it point), and does its
        network stream play (size, codec, frame rate, decode time)? Everything here is measured."""
        from trackxen.devices import visca
        from trackxen.vision.sources import StreamSource

        result: dict = {"visca": None, "video": None}
        if cam.host:
            client = visca.open_client(cam.host, cam.visca_port, cam.visca_transport, timeout=1.0)
            try:
                await client.connect()
                t0 = time.perf_counter()
                pan, tilt = await client.pan_tilt_position()
                rtt = (time.perf_counter() - t0) * 1000
                zoom = await client.zoom_position()
                result["visca"] = {"ok": True, "rtt_ms": round(rtt, 1), "pan": round(pan / cam.units_per_degree, 1),
                                   "tilt": round(tilt / (cam.tilt_units_per_degree or cam.units_per_degree), 1),
                                   "zoom": zoom}
            except (visca.ViscaError, OSError, ValueError) as e:
                result["visca"] = {"ok": False, "error": str(e) or type(e).__name__}
            finally:
                client.close()
        if cam.video == "stream" and cam.video_url:
            def play() -> dict:
                src = StreamSource(cam.video_url, cam.stream_transport, timeout=4.0)
                src.start()
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline and src.frames_received < 45:
                    if src.last_error and not src.connected and time.monotonic() > deadline - 4.0:
                        break  # 4 seconds of failing to connect: report that
                    time.sleep(0.05)
                arrivals = list(src.arrivals)
                fps = (len(arrivals) - 1) / (arrivals[-1] - arrivals[0]) if len(arrivals) > 2 else 0.0
                src.stop()
                if not src.frames_received:
                    return {"ok": False, "error": src.last_error or "no picture within 8 seconds", "url": src.source_name}
                return {"ok": True, "url": src.source_name, "width": src.size[0], "height": src.size[1],
                        "codec": src.codec, "fps": round(fps, 1), "decode_ms": src.decode_ms,
                        "damaged": src.ndi_frames_dropped}

            result["video"] = await asyncio.to_thread(play)
        return result

    def run_hardware_test(self, cameras: int, fps: float, shared_with_vmix: bool, quick: bool) -> bool:
        """Start the hardware test in the background (progress lines appear in self.hwtest)."""
        if self.hwtest["running"]:
            return False
        if self.gpu_libs and self.gpu_libs.state in ("checking", "downloading", "failed"):
            self.hwtest = {"running": False, "report": None,
                           "lines": ["The hardware test waits for NVIDIA's libraries: they are still being downloaded "
                                     "(without them it would only measure the processor)."]}
            return False
        from trackxen.core import hwtest

        self.hwtest = {"running": True, "lines": [], "report": None}

        def progress(line: str) -> None:
            self.hwtest["lines"].append(line)

        def work() -> None:
            try:
                report = hwtest.run(self.models, cameras, fps, shared_with_vmix, tensorrt=not quick,
                                    cache_dir=engine_cache_dir(),
                                    progress=progress)
                report["text_file"] = str(hwtest.save(report, self.data / "hardware-tests"))
                self.hwtest["report"] = report
                self.log.event("hardware test", report["recommendation"]["mode"].upper() + " recommended")
            except Exception as e:  # noqa: BLE001 - show the failure in the UI
                progress(f"hardware test failed: {e}")
            finally:
                self.hwtest["running"] = False

        threading.Thread(target=work, name="hardware-test", daemon=True).start()
        self.log.event("hardware test", "started")
        return True

"""Pan/tilt smoothness bench: TrackXen's real tracking code follows a simulated person, in virtual time.

  python tools/motion_bench.py                         the standard cases, 3 seeds each: smoothness and framing table
  python tools/motion_bench.py --check                 ... and exit 1 when a limit breaks (see --check below)
  python tools/motion_bench.py walk1.2-p400-8-air      one case: scenario-camera-field of view[-air][-nohalt]
  python tools/motion_bench.py --slot smoothness=10    another App Settings value (any field, JSON values)
  python tools/motion_bench.py --motion max_accel=30   another motion profile value (Settings -> Cameras, motion)
  python tools/motion_bench.py --json a.json           save the results; --baseline a.json compares framing with them

A case is a scenario (walk1.2 = across the stage and back at 1.2 m/s; walk0.5; stopgo = five short walks with
pauses; stand = standing, shifting the weight; approach = towards the camera from 20 m to 8 m; drift0.15 = a slow
sideways drift), a camera (p400 or g2), the horizontal field of view in degrees, and optionally -air (on air,
On-Air Motion at its defaults) and -nohalt (Pan/Tilt Halting Area off). The person is 15 m away (approach:
20 m to 8 m). Everything else is the camera type's and App Settings' defaults (Pan/Tilt Smoothness 5).

Real TrackXen code in the loop: CameraSession (App Settings, halting area, on-air blend, tracks, the 50 Hz control
step), Controller, AngleKalman, Lens, CameraLink (command coalescing, the send loop, FreeD history on the P400,
20 Hz position polls on the G2), ViscaClient, FreeD, FakeCamera's VISCA, PersonTracker (ByteTrack and its box
filter), the vision worker's detection-rate limit, and the camera types' speed tables, lens and video delay.
Simulated: the clock (virtual, so a 30 s case takes about a second), UDP (in memory, 1 ms), the camera's own
speed ramp (two 40 ms lags), video delay and jitter at 30 fps, 15 detections a second (Light), detection
latency, and the person: path, walking head bob and sway, head turns, ear keypoint confidence flicker, looking
down (face lost), keypoint and box noise. Each run is seeded, so the same code gives the same numbers (with the
same Python and numpy). Single seeds scatter: a mean over 3 seeds is what the limits are set for.

Columns, per axis (pan: % of the picture width, tilt: of its height), on the steady walking parts of walk and
drift (from 1.5 s into each walk), on everything after the first 4 s otherwise:
  chg/s   speed step changes the camera carried out, per second     jmp/s  ... of more than one step
  strt/m  starts from standstill per minute                          rev/m  reversals per minute
  stop%   share of the time standing still                           rippl  speed ripple (% per s, against 1 s mean)
  acc     acceleration RMS (% per s^2)                               jerk   jerk RMS (% per s^3)
  e_rms / e_p95 / e_max  the true head's distance from the display position
Tilt rows are left out where the tilt hardly moved (under 0.3 step changes a second).

--check: while walking steadily the pan neither restarts (at most 10 starts a minute) nor stands still (12 %);
standing and stop-and-go have their own start limits; with the Halting Area off the tilt does not hunt (30
reversals a minute); the head is never more than 25 % of the picture from its display position; and framing
(err_p95) stays within REFERENCE x 1.25 + 0.5, REFERENCE being this tool's own measurement of the standard cases
when it was added. At 0d953f9 (before the smoothness work) 21 limits break; now none at Pan/Tilt Smoothness 1
and 5, and three at 10 (a brisk walker in a tight shot on air: the cost of the softest level).

Not a camera test: the real cameras' speed tables, internal ramps and delays are estimates until measured
with visca_probe.py (sweep, ramp) and stream_probe.py. The bench shows what TrackXen's own motion does.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import math
import os
import selectors
import sys
import time
import types
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np

from trackxen.control.camera import CameraLink, CameraLinkConfig
from trackxen.control.lens import Lens
from trackxen.core.camera_models import camera_defaults
from trackxen.core.config import CameraConfig
from trackxen.core.session import CameraSession
from trackxen.devices import udp
from trackxen.sim.fake_camera import FakeCamera
from trackxen.vision.tracker import PersonTracker
from trackxen.vision.worker import VisionLoop
from trackxen.vision.yolo_pose import Person

W, H = 1920, 1080
CAMERAS = {  # the camera type, and how the simulated one answers
    "p400": dict(model="birddog_p400", reply_delay=0.003, command_delay=0.03, freed_rate=50.05),
    "g2": dict(model="ptzoptics_g2", reply_delay=0.008, command_delay=0.05, freed_rate=0.0),
}


# --- virtual time and in-memory UDP, only while a case runs --------------------------------------------------

class _World:
    def __init__(self, start: float = 1000.0, one_way: float = 0.001):
        self.now, self.one_way = start, one_way
        self.endpoints: dict[int, _MemTransport] = {}
        self._next_port = 50000

    def monotonic(self) -> float:
        return self.now

    async def open_udp(self, protocol_factory, local=("0.0.0.0", 0), broadcast=False):
        if not local[1]:
            self._next_port += 1
        proto = protocol_factory()
        transport = _MemTransport(self, local[1] or self._next_port, proto)
        proto.connection_made(transport)
        return transport, proto


class _MemTransport(asyncio.DatagramTransport):
    def __init__(self, world: _World, port: int, protocol):
        super().__init__()
        self.world, self.port, self.protocol, self._closing = world, port, protocol, False
        world.endpoints[port] = self

    def sendto(self, data, addr=None):
        dst = self.world.endpoints.get(addr[1]) if addr else None
        if dst is None or self._closing:
            return
        data, src = bytes(data), ("127.0.0.1", self.port)

        def deliver():
            if not dst._closing:
                dst.protocol.datagram_received(data, src)
        asyncio.get_running_loop().call_later(self.world.one_way, deliver)

    def close(self):
        if not self._closing:
            self._closing = True
            self.world.endpoints.pop(self.port, None)

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return ("127.0.0.1", self.port) if name == "sockname" else default

    def abort(self):
        self.close()


class _VirtualSelector(selectors.SelectSelector):
    """No real I/O: waiting just moves the virtual clock on."""

    def __init__(self, world: _World):
        super().__init__()
        self.world = world

    def select(self, timeout=None):
        if timeout is None:
            raise RuntimeError("the simulation would wait forever")
        if timeout > 0:
            self.world.now += timeout
        return []


@contextlib.contextmanager
def _virtual(world: _World):
    """time.monotonic and UDP sockets are the world's while a case runs, and put back afterwards. (The engine
    reads time.monotonic through the module and imports open_udp where it opens a socket.)"""
    real_monotonic, real_open_udp = time.monotonic, udp.open_udp
    time.monotonic, udp.open_udp = world.monotonic, world.open_udp
    try:
        yield
    finally:
        time.monotonic, udp.open_udp = real_monotonic, real_open_udp


# --- the camera: FakeCamera with a speed ramp -----------------------------------------------------------------

class RampCamera(FakeCamera):
    """FakeCamera whose real speed follows the commanded one through two first-order lags of `ramp_tau` (a
    motor's own smooth ramp), and whose replies take `reply_delay`. Logs its motion and the drives it carried out."""

    def __init__(self, *a, ramp_tau: float = 0.04, reply_delay: float = 0.0, **kw):
        super().__init__(*a, **kw)
        self.ramp_tau, self.reply_delay = ramp_tau, reply_delay
        self.r = [0.0, 0.0, 0.0, 0.0]  # pan lag 1, pan lag 2 (the real speed), tilt lag 1, tilt lag 2
        self.log: list[tuple] = []  # t, pan, tilt, zoom, real pan speed, real tilt speed
        self.drives: list[tuple[float, int, int]] = []  # t, signed pan step, signed tilt step

    def step(self, dt: float) -> None:
        s = self.state
        if s.target is None and dt > 0:
            k = min(1.0, dt / self.ramp_tau) if self.ramp_tau > 0 else 1.0
            self.r[0] += (s.pan_rate - self.r[0]) * k
            self.r[1] += (self.r[0] - self.r[1]) * k
            self.r[2] += (s.tilt_rate - self.r[2]) * k
            self.r[3] += (self.r[2] - self.r[3]) * k
            commanded = s.pan_rate, s.tilt_rate
            s.pan_rate, s.tilt_rate = self.r[1], self.r[3]
            super().step(dt)
            s.pan_rate, s.tilt_rate = commanded
        else:
            super().step(dt)
            self.r = [0.0, 0.0, 0.0, 0.0]
        self.log.append((time.monotonic(), s.pan, s.tilt, s.zoom, self.r[1], self.r[3]))

    def _reply(self, addr, payload_type, payload, seq):
        if self.reply_delay > 0:
            asyncio.get_running_loop().call_later(self.reply_delay, super()._reply, addr, payload_type, payload, seq)
        else:
            super()._reply(addr, payload_type, payload, seq)

    def _execute(self, msg, addr, seq):
        super()._execute(msg, addr, seq)
        if msg[1:4] == b"\x01\x06\x01":
            ps, ts, pd, td = msg[4], msg[5], msg[6], msg[7]
            self.drives.append((time.monotonic(), {1: -ps, 2: ps}.get(pd, 0), {1: ts, 2: -ts}.get(td, 0)))


# --- the person -------------------------------------------------------------------------------------------------

def _smoothstep(u):
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3 - 2 * u)


@dataclass
class Leg:
    t0: float
    t1: float
    x0: float
    x1: float
    d0: float
    d1: float
    moving: bool


def build_path(kind: str, speed: float, distance: float, duration: float) -> tuple[list[Leg], float]:
    """The body centre's path: sideways x (m) and distance d (m), in legs. A walk eases in and out over 0.2 s."""
    legs: list[Leg] = []
    t = 0.0

    def stand(dt, x, d):
        nonlocal t
        legs.append(Leg(t, t + dt, x, x, d, d, False))
        t += dt

    def walk(x0, x1, d0, d1, v):
        nonlocal t
        dt = math.hypot(x1 - x0, d1 - d0) / v + 0.4  # (the eased ends cost 0.4 s in all)
        legs.append(Leg(t, t + dt, x0, x1, d0, d1, True))
        t += dt

    d = distance
    if kind == "walk":  # across the stage and back
        x = max(3.0, speed * 5.0)
        stand(4.0, -x, d)
        walk(-x, x, d, d, speed)
        stand(2.5, x, d)
        walk(x, -x, d, d, speed)
        stand(3.0, -x, d)
    elif kind == "drift":  # a slow drift sideways for 20 s
        stand(4.0, -1.5, d)
        walk(-1.5, -1.5 + speed * 20, d, d, speed)
        stand(3.0, -1.5 + speed * 20, d)
    elif kind == "stopgo":  # five short walks with pauses
        x = -6.0
        stand(4.0, x, d)
        for _ in range(5):
            walk(x, x + speed * 2.5, d, d, speed)
            x += speed * 2.5
            stand(2.5, x, d)
    elif kind == "stand":
        stand(duration, 0.0, d)
    elif kind == "approach":  # towards the camera (size and tilt change), drifting sideways a little
        stand(4.0, -1.5, 20.0)
        walk(-1.5, 1.5, 20.0, 8.0, speed)
        stand(3.0, 1.5, 8.0)
    else:
        raise ValueError(f"unknown scenario {kind!r}")
    return legs, t


@dataclass
class PersonModel:
    kind: str = "walk"
    speed: float = 1.2  # m/s
    distance: float = 15.0  # m
    duration: float = 25.0  # s, for "stand"
    height: float = 1.75
    eye_height: float = 1.63
    cam_height: float = 2.2
    seed: int = 1
    sway_sigma: float = 0.03  # m, body sway
    sway_tau: float = 1.5
    yaw_sigma: float = 20.0  # degrees, turning the head towards the congregation
    yaw_tau: float = 3.0
    bob: float = 0.02  # m, the head's bob while walking (1.8 Hz)
    weight_shift: float = 0.0  # m, standing: from one foot to the other every 4-8 s
    kp_px: float = 2.5  # keypoint jitter (1080p pixels) ...
    kp_rel: float = 0.015  # ... plus this share of the head's height
    box_rel: float = 0.012  # box edge jitter, share of the person's height
    ear_base: float = 0.5  # ear keypoint confidence for a frontal face (head_point uses those >= 0.4)
    ear_noise: float = 0.12
    face_lost_rate: float = 1 / 20  # per second: looks down at notes or turns away (no face keypoints)
    face_lost_len: tuple[float, float] = (0.4, 1.5)
    p_miss: float = 0.0  # missed detections, share of frames
    noise: bool = True

    def __post_init__(self):
        self.legs, total = build_path(self.kind, self.speed, self.distance, self.duration)
        self.duration = max(self.duration, total) if self.kind == "stand" else total
        rng = np.random.default_rng(self.seed)
        self.dt = 0.005
        n = int(self.duration / self.dt) + 400
        self.grid = np.arange(n) * self.dt

        def ou(sigma, tau, smooth=0.0):
            """Ornstein-Uhlenbeck noise; `smooth` > 0 passes it through one more lag (a body moves smoothly),
            scaled back to `sigma`."""
            out = np.zeros(n)
            a = math.exp(-self.dt / tau)
            b = sigma * math.sqrt(1 - a * a)
            e = rng.standard_normal(n)
            for i in range(1, n):
                out[i] = a * out[i - 1] + b * e[i]
            if smooth > 0:
                k = self.dt / smooth
                y = np.zeros(n)
                for i in range(1, n):
                    y[i] = y[i - 1] + (out[i] - y[i - 1]) * k
                out = y * (sigma / max(1e-9, y.std())) if y.std() > 0 else y
            return out

        self.sway = ou(self.sway_sigma, self.sway_tau, 0.4)
        self.yaw = ou(self.yaw_sigma, self.yaw_tau, 0.3)
        self.ear_l = ou(self.ear_noise, 0.15)
        self.ear_r = ou(self.ear_noise, 0.15)
        self.lost = np.zeros(n, dtype=bool)
        t = 2.0
        while t < self.duration and self.face_lost_rate > 0:
            t += rng.exponential(1 / self.face_lost_rate)
            length = rng.uniform(*self.face_lost_len)
            self.lost[int(t / self.dt):int((t + length) / self.dt)] = True
            t += length
        self.shift = np.zeros(n)
        if self.weight_shift > 0:  # from one foot to the other and back, each shift taking 0.6 s
            t, side = 2.0, 1
            while True:
                t += rng.uniform(4.0, 8.0)
                i0 = int(t / self.dt)
                if i0 >= n:
                    break
                self.shift[i0:] += _smoothstep((self.grid[i0:] - t) / 0.6) * self.weight_shift * side
                side = -side
        self.rng = np.random.default_rng(self.seed + 1000)

    def _i(self, t: float) -> int:
        return min(len(self.grid) - 1, max(0, int(round(t / self.dt))))

    def body(self, t: float) -> tuple[float, float, float]:
        """Sideways x, distance d (m) and walking speed (m/s) at t."""
        for leg in self.legs:
            if t < leg.t1 or leg is self.legs[-1]:
                if not leg.moving:
                    return leg.x0, leg.d0, 0.0
                T = leg.t1 - leg.t0
                u = min(max((t - leg.t0) / T, 0.0), 1.0)
                e = 0.2 / T  # ease in, cruise, ease out
                if u < e:
                    f, sp = u * u / (2 * e * (1 - e)), u / (e * (1 - e))
                elif u > 1 - e:
                    f, sp = 1 - (1 - u) ** 2 / (2 * e * (1 - e)), (1 - u) / (e * (1 - e))
                else:
                    f, sp = (u - e / 2) / (1 - e), 1 / (1 - e)
                length = math.hypot(leg.x1 - leg.x0, leg.d1 - leg.d0)
                return leg.x0 + (leg.x1 - leg.x0) * f, leg.d0 + (leg.d1 - leg.d0) * f, sp * length / T
        raise AssertionError

    def moving_steady(self, t: float, settle: float = 1.5) -> bool:
        """In a walk, from `settle` s after it started to 0.2 s before it ends (walking steadily)."""
        return any(leg.moving and leg.t0 + settle <= t <= leg.t1 - 0.2 for leg in self.legs)

    def head(self, t: float) -> tuple[float, float, float, float, float]:
        """The head (between the eyes): x, z, d (m), yaw (degrees), and the body's x."""
        x, d, sp = self.body(t)
        i = self._i(t)
        walking = min(1.0, sp / 1.0)
        sway = self.sway[i] + self.shift[i]
        hx = x + sway + 0.012 * walking * math.sin(math.pi * 1.8 * t)
        hz = self.eye_height + self.bob * walking * math.sin(2 * math.pi * 1.8 * t)
        return hx, hz, d, self.yaw[i], x + sway

    def angles(self, x: float, z: float, d: float) -> tuple[float, float]:
        return math.degrees(math.atan2(x, d)), math.degrees(math.atan2(z - self.cam_height, math.hypot(x, d)))

    def true_head_angles(self, t: float) -> tuple[float, float]:
        hx, hz, d, _, _ = self.head(t)
        return self.angles(hx, hz, d)

    def detect(self, t: float, pose: tuple[float, float, float], lens: Lens) -> list[Person]:
        """What the detector finds in the frame captured at t with the camera at `pose` (pan, tilt, zoom)."""
        rng = self.rng
        if self.p_miss and rng.random() < self.p_miss:
            return []
        pan_c, tilt_c, zoom = pose
        hx, hz, d, yaw, bodyx = self.head(t)
        i = self._i(t)

        def px(x, z):
            p, q = self.angles(x, z, d)
            return lens.angles_to_pixel(p - pan_c, q - tilt_c, W, H, zoom)

        top, feet = px(bodyx, self.height), px(bodyx, 0.0)
        left, right = px(bodyx - 0.24, 1.2)[0], px(bodyx + 0.24, 1.2)[0]
        h_px = feet[1] - top[1]
        head_px = 0.23 / self.height * h_px
        box_sd = self.box_rel * h_px if self.noise else 0.0
        x1, x2 = max(0.0, left + rng.normal(0, box_sd)), min(float(W), right + rng.normal(0, box_sd))
        y1, y2 = max(0.0, top[1] + rng.normal(0, box_sd)), min(float(H), feet[1] + rng.normal(0, box_sd))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return []
        kp = np.zeros((17, 3))
        s, c = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        face = [(0.10 * s, -0.04), (-0.032 * c + 0.08 * s, 0.0), (0.032 * c + 0.08 * s, 0.0),
                (-0.075 * c - 0.01 * s, -0.025), (0.075 * c - 0.01 * s, -0.025)]  # nose, eyes, ears
        lost = bool(self.lost[i])
        conf = [0.9, 0.9, 0.9,
                min(0.95, max(0.0, self.ear_base + 0.8 * s + self.ear_l[i])),
                min(0.95, max(0.0, self.ear_base - 0.8 * s + self.ear_r[i]))]
        if abs(yaw) > 60:
            conf[2 if yaw > 0 else 1] = 0.3
        kp_sd = (self.kp_px + self.kp_rel * head_px) if self.noise else 0.0
        for k, ((dx, dz), cf) in enumerate(zip(face, conf)):
            u, v = px(hx + dx, hz + dz)
            kp[k] = (u + rng.normal(0, kp_sd), v + rng.normal(0, kp_sd), 0.15 if lost else cf)
        body = [(-0.2, 1.45), (0.2, 1.45), (-0.25, 1.15), (0.25, 1.15), (-0.25, 0.9), (0.25, 0.9),
                (-0.12, 0.95), (0.12, 0.95), (-0.12, 0.5), (0.12, 0.5), (-0.12, 0.08), (0.12, 0.08)]
        for k, (dx, z) in zip(range(5, 17), body):
            u, v = px(bodyx + dx, z)
            kp[k] = (u, v, 0.9 if 0 <= u <= W and 0 <= v <= H else 0.05)
        return [Person(np.array([x1, y1, x2, y2]), 0.85, kp)]


# --- one run ----------------------------------------------------------------------------------------------------

@dataclass
class RunSpec:
    camera: str = "p400"  # p400 | g2
    hfov: float = 50.0  # degrees
    person: PersonModel = field(default_factory=PersonModel)
    on_air: bool = False
    halting_area: bool = True
    slot: dict = field(default_factory=dict)  # App Settings patch (e.g. {"smoothness": 10})
    motion: dict = field(default_factory=dict)  # CameraConfig.motion fields
    detect_fps: float = 15.0  # performance preset Light; Normal / High: 30
    source_fps: float = 30.0
    proc_latency: float = 0.025  # s: detection, tracking and passing the result on
    delay_jitter: float = 0.004  # s, sd of when a frame arrives
    ramp_tau: float = 0.04  # s, the camera's own speed ramp (two lags)
    eval_from: float = 4.0  # s: what comes before is the lock-on


def run(spec: RunSpec) -> dict:
    """One case with one seed: the metrics per axis and window."""
    world = _World()
    with _virtual(world):
        loop = asyncio.SelectorEventLoop(_VirtualSelector(world))
        try:
            return loop.run_until_complete(_run(spec))
        finally:
            loop.close()


async def _run(spec: RunSpec) -> dict:
    cam_kind = CAMERAS[spec.camera]
    cfg = CameraConfig(id="cam1", host="127.0.0.1", **camera_defaults(cam_kind["model"], 1))
    if spec.motion:
        cfg.motion = dataclasses.replace(cfg.motion, **spec.motion)
    lens = Lens(cfg.lens.hfov_wide, cfg.lens.hfov_tele, cfg.lens.zoom_max)
    plain = cfg.visca_transport != "sony"
    cam = RampCamera(freed_target=("127.0.0.1", cfg.freed_port) if cfg.freed_port else None,
                     freed_rate=cam_kind["freed_rate"] or 50.0, command_delay=cam_kind["command_delay"],
                     ramp_tau=spec.ramp_tau, reply_delay=cam_kind["reply_delay"], plain=plain,
                     pan_units=cfg.units_per_degree, tilt_units=cfg.tilt_units_per_degree or cfg.units_per_degree,
                     pan_table=list(cfg.pan_speeds), tilt_table=list(cfg.tilt_speeds))
    await cam.start("127.0.0.1", cfg.visca_port)
    link = CameraLink("cam1", CameraLinkConfig(
        host=cfg.host, visca_port=cfg.visca_port, freed_port=cfg.freed_port, transport=cfg.visca_transport,
        units_per_degree=cfg.units_per_degree, tilt_units_per_degree=cfg.tilt_units_per_degree,
        pan_sign=cfg.pan_sign, tilt_sign=cfg.tilt_sign,
        pan_speed_max=len(cfg.pan_speeds), tilt_speed_max=len(cfg.tilt_speeds)))
    await link.start()

    class Log:
        def event(self, *args, **kw):
            pass

    session = CameraSession(cfg, link, log=Log())
    session.update_slot({"halting_area": spec.halting_area, **spec.slot})
    if spec.on_air:
        session.on_air_manual = True
        session.air = 1.0
        session._apply_air()

    person = spec.person
    zoom = lens.zoom_for_hfov(spec.hfov)
    hp, ht = person.true_head_angles(0.0)
    comp = session.slot.composition  # start framed: the head at the display position
    off_p, off_t = lens.pixel_to_angles(comp.anchor_x * W, comp.anchor_y * H, W, H, zoom)
    cam.state.pan, cam.state.tilt, cam.state.zoom = hp - off_p, ht - off_t, zoom
    await asyncio.sleep(0.3)  # FreeD or the position polls fill the history

    t0 = time.monotonic()
    pose_err = {"obs": [], "control": []}  # pan position error of what the controller was given, % of the width
    ctrl = session.controller
    real_observe, real_command = ctrl.observe, ctrl.command

    def observe(obs, pose):
        true = cam.pose_at(obs.t)
        pose_err["obs"].append((pose.pan - true[0]) / lens.hfov(true[2]) * 100)
        return real_observe(obs, pose)

    def command(now, pose, *a, **kw):
        pose_err["control"].append((pose.pan - cam.state.pan) / lens.hfov(cam.state.zoom) * 100)
        return real_command(now, pose, *a, **kw)
    ctrl.observe, ctrl.command = observe, command

    tracker = PersonTracker()
    bucket = types.SimpleNamespace(max_detect_fps=spec.detect_fps, _last_detect={}, _tokens={})
    rng = np.random.default_rng(person.seed + 77)
    observations = 0

    async def vision():
        nonlocal observations
        k, busy_until, selected = 0, 0.0, False
        while True:
            captured = t0 + k / spec.source_fps
            arrives = captured + cfg.video_delay + abs(rng.normal(0, spec.delay_jitter))
            k += 1
            if arrives > time.monotonic():
                await asyncio.sleep(arrives - time.monotonic())
            now = time.monotonic()
            if now < busy_until or not VisionLoop._allowed(bucket, "cam1", now):
                continue  # still busy with the previous frame, or over the detection rate
            busy_until = now + spec.proc_latency
            people = person.detect(captured - t0, cam.pose_at(captured), lens)
            await asyncio.sleep(spec.proc_latency)
            tracks = tracker.update(people, now)
            session.on_tracks({"type": "tracks", "camera": "cam1", "frame": k, "t_frame": now, "t_done": time.monotonic(),
                               "width": W, "height": H, "tracks": [t.to_dict() for t in tracks], "skipped": 0,
                               "timing": {"infer_ms": 10.0}, "engine": "sim", "batch": 1})
            observations += 1
            if not selected and tracks:
                session.select_id(tracks[0].id)
                session.set_tracking(True, "motion bench", start_at_initial=False)
                selected = True

    async def control():
        while True:
            session.tick(time.monotonic())
            await asyncio.sleep(0.02)

    tasks = [asyncio.create_task(vision()), asyncio.create_task(control())]
    await asyncio.sleep(person.duration)
    for task in tasks:
        if task.done() and task.exception():
            raise task.exception()
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await link.close()
    cam.stop()

    camlog = np.array([r for r in cam.log if r[0] >= t0])
    drives = [(t - t0, p, q) for t, p, q in cam.drives if t >= t0]
    out = evaluate(spec, person, lens, camlog, t0, drives, session.slot.composition)
    out["pose"] = {key: float(np.sqrt(np.mean(np.square(v)))) if v else 0.0 for key, v in pose_err.items()}
    out["pose"]["observations_per_s"] = observations / person.duration
    return out


# --- metrics ----------------------------------------------------------------------------------------------------

def _moving_average(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x.copy()
    pad = n // 2
    return np.convolve(np.pad(x, (pad, n - 1 - pad), mode="edge"), np.ones(n) / n, mode="valid")


def _last_direction(seq, t) -> int:
    """The sign of the last nonzero step before t, if the camera stopped less than 0.6 s before."""
    prev, stop_t = 0, None
    for ts, a, b in seq:
        if ts >= t:
            break
        if b == 0 and a != 0:
            prev, stop_t = a, ts
        elif b != 0:
            prev, stop_t = b, None
    return 0 if stop_t is not None and t - stop_t > 0.6 else prev


def step_stats(drives, axis: int, inside, t_from: float, t_to: float) -> dict:
    """Changes of the speed step the camera carried out on one axis, in [t_from, t_to] where inside(t)."""
    seq, last = [], 0
    for t, p, q in drives:
        v = p if axis == 0 else q
        if v != last:
            seq.append((t, last, v))
            last = v
    sel = [(t, a, b) for t, a, b in seq if t_from <= t <= t_to and inside(t)]
    return {"changes": len(sel), "jumps": sum(1 for _, a, b in sel if abs(b - a) > 1),
            "starts": sum(1 for _, a, b in sel if a == 0 and b != 0),
            "reversals": sum(1 for t, a, b in sel if a * b < 0 or (a == 0 and b != 0 and _last_direction(seq, t) * b < 0))}


def evaluate(spec, person, lens, camlog, t0, drives, comp) -> dict:
    t = camlog[:, 0] - t0
    pan, tilt, zoom, vp, vt = camlog[:, 1], camlog[:, 2], camlog[:, 3], camlog[:, 4], camlog[:, 5]
    hfov = np.array([lens.hfov(z) for z in zoom])
    vfov = np.array([lens.vfov(z) for z in zoom])
    ex, ey = np.zeros(len(t)), np.zeros(len(t))
    for i in range(len(t)):  # where the true head is in the picture, against the display position
        hp, ht = person.true_head_angles(t[i])
        x, y = lens.angles_to_pixel(hp - pan[i], ht - tilt[i], W, H, zoom[i])
        ex[i], ey[i] = x / W - comp.anchor_x, y / H - comp.anchor_y
    dt = np.median(np.gradient(t))
    later = t >= spec.eval_from
    windows = {"all": later, "steady": later & np.array([person.moving_steady(x) for x in t])}
    out = {"duration": float(t[-1])}
    for axis, (v, fov, e, name) in enumerate(((vp, hfov, ex, "pan"), (vt, vfov, ey, "tilt"))):
        vpic = v / fov * 100.0  # % of the picture per second: what a viewer sees
        acc = np.gradient(vpic, t)
        jerk = np.gradient(acc, t)
        ripple = vpic - _moving_average(vpic, int(1.0 / dt))
        for wname, m in windows.items():
            if m.sum() < 50:
                continue
            dur, tm = float(m.sum() * dt), t[m]
            st = step_stats(drives, axis, lambda x, mm=m: mm[min(len(t) - 1, max(0, int(np.searchsorted(t, x))))],
                            float(tm[0]), float(tm[-1]))
            out[f"{name}.{wname}"] = {
                "chg_s": st["changes"] / dur, "jump_s": st["jumps"] / dur,
                "starts_min": st["starts"] / dur * 60, "rev_min": st["reversals"] / dur * 60,
                "stopped": float(np.mean(np.abs(v[m]) < 0.05)),
                "ripple": float(np.sqrt(np.mean(ripple[m] ** 2))),
                "acc_rms": float(np.sqrt(np.mean(acc[m] ** 2))),
                "jerk_rms": float(np.sqrt(np.mean(jerk[m] ** 2))),
                "err_rms": float(np.sqrt(np.mean(e[m] ** 2))) * 100,
                "err_p95": float(np.percentile(np.abs(e[m]), 95)) * 100,
                "err_max": float(np.max(np.abs(e[m]))) * 100,
            }
    return out


# --- cases ------------------------------------------------------------------------------------------------------

SCENARIOS = {  # kind: (default speed m/s, window, weight shift m)
    "walk": (1.2, "steady", 0.0), "drift": (0.15, "steady", 0.0), "stopgo": (1.0, "all", 0.0),
    "stand": (0.0, "all", 0.15), "approach": (0.7, "all", 0.0),
}
KEYS = ["chg_s", "jump_s", "starts_min", "rev_min", "stopped", "ripple", "acc_rms", "jerk_rms", "err_rms", "err_p95",
        "err_max"]


@dataclass(frozen=True)
class Case:
    scenario: str  # e.g. walk1.2
    camera: str
    hfov: float
    on_air: bool = False
    halting_area: bool = True

    @property
    def name(self) -> str:
        return (f"{self.scenario}-{self.camera}-{self.hfov:g}" + ("-air" if self.on_air else "")
                + ("" if self.halting_area else "-nohalt"))

    @property
    def kind(self) -> str:
        return self.scenario.rstrip("0123456789.")

    @classmethod
    def parse(cls, text: str) -> Case:
        parts = text.split("-")
        if len(parts) < 3 or not set(parts[3:]) <= {"air", "nohalt"}:
            raise ValueError(f"{text!r}: scenario-camera-hfov[-air][-nohalt], e.g. walk1.2-p400-8-air")
        case = cls(parts[0], parts[1], float(parts[2]), "air" in parts[3:], "nohalt" not in parts[3:])
        if case.kind not in SCENARIOS or case.camera not in CAMERAS:
            raise ValueError(f"{text!r}: scenarios {', '.join(SCENARIOS)}; cameras {', '.join(CAMERAS)}")
        float(case.scenario[len(case.kind):] or 0)
        return case

    def spec(self, seed: int, slot: dict, motion: dict, detect_fps: float) -> RunSpec:
        speed, _, shift = SCENARIOS[self.kind]
        speed = float(self.scenario[len(self.kind):] or speed)
        return RunSpec(camera=self.camera, hfov=self.hfov, on_air=self.on_air, halting_area=self.halting_area,
                       person=PersonModel(kind=self.kind, speed=speed, weight_shift=shift, seed=seed),
                       slot=dict(slot), motion=dict(motion), detect_fps=detect_fps)


STANDARD = [Case.parse(c) for c in (
    "walk1.2-p400-50", "walk0.5-p400-50", "walk1.2-p400-8", "walk0.5-p400-8", "walk1.2-g2-50", "walk0.5-g2-8",
    "walk0.5-p400-50-air", "walk1.2-p400-8-air", "walk1.2-g2-8-air", "stopgo-p400-8", "stand-p400-8", "stand-g2-8",
    "walk0.5-p400-8-nohalt", "walk1.2-g2-50-nohalt")]
SEEDS = (1, 2, 3)

# Smoothness limits (pan), per scenario kind. Before 2026-11-04 (0d953f9) a steady walk made 17-48 starts a minute
# and the camera stood still 9-59 % of the walking time; now 0-6.5 starts and 0-7 % (at Pan/Tilt Smoothness 5).
SMOOTH_LIMITS = {
    "walk": {"starts_min": 10.0, "stopped": 0.12},  # a steady walk is followed in one movement
    "stand": {"starts_min": 30.0},  # (shifting the weight at 8 degrees leaves the halting area, rightly)
    "stopgo": {"starts_min": 20.0},  # (the person starts 5 times in 27 s: 11 a minute)
}
# A slow walker in a tight G2 shot turns the camera at 1.9 deg/s, between the G2's two slowest pan steps (1.70 and
# 2.03 deg/s): measured over 10 seeds 9.3 starts a minute on average, 0-19.6 per seed (3 seeds: 11.2; 0d953f9: 43.9).
# The P400 makes none at the same shot.
CASE_LIMITS = {"walk0.5-g2-8": {"starts_min": 15.0}}
NOHALT_TILT_LIMITS = {"rev_min": 30.0}  # Halting Area off: the tilt must not hunt the head bob (0d953f9: 88-212)
FAR = 25.0  # % of the picture: the head stays within a quarter of the picture of its display position (0d953f9 lost
# a 1.2 m/s walker at 8 degrees on air: hundreds of %)
# Framing reference: err_p95 (pan, tilt) of the standard cases at 3 seeds, measured with this tool when it was added
# (2026-11-04, Pan/Tilt Smoothness 5). --check allows REFERENCE x 1.25 + 0.5.
REFERENCE = {
    "walk1.2-p400-50": (1.01, 0.73), "walk0.5-p400-50": (2.30, 0.25), "walk1.2-p400-8": (6.33, 2.88),
    "walk0.5-p400-8": (3.43, 1.64), "walk1.2-g2-50": (1.05, 0.73), "walk0.5-g2-8": (4.15, 1.66),
    "walk0.5-p400-50-air": (7.89, 0.25), "walk1.2-p400-8-air": (6.49, 4.84), "walk1.2-g2-8-air": (6.61, 4.84),
    "stopgo-p400-8": (16.78, 3.49), "stand-p400-8": (5.64, 0.00), "stand-g2-8": (6.19, 0.00),
    "walk0.5-p400-8-nohalt": (3.47, 2.58), "walk1.2-g2-50-nohalt": (0.96, 0.56),
}
REFERENCE_TOLERANCE = (1.25, 0.5)


def one(job) -> dict:
    case, seed, slot, motion, detect_fps = job
    try:
        r = run(case.spec(seed, slot, motion, detect_fps))
    except Exception:  # noqa: BLE001  (reported with its case, the other cases still run)
        import traceback
        return {"case": case.name, "seed": seed, "error": traceback.format_exc()}
    window = SCENARIOS[case.kind][1]
    out = {"case": case.name, "seed": seed, "pose": r["pose"]}
    for axis in ("pan", "tilt"):
        m = r.get(f"{axis}.{window}") or r[f"{axis}.all"]
        out[axis] = {k: m[k] for k in KEYS}
    return out


def run_cases(cases: list[Case], seeds=SEEDS, slot=None, motion=None, detect_fps: float = 15.0,
              jobs: int = 1) -> list[dict]:
    work = [(case, seed, slot or {}, motion or {}, detect_fps) for case in cases for seed in seeds]
    if jobs <= 1:
        return [one(w) for w in work]
    with Pool(jobs) as pool:
        return pool.map(one, work, chunksize=1)


def summarize(results: list[dict]) -> dict[str, dict]:
    """Means over the seeds, per case."""
    groups: dict[str, list[dict]] = {}
    for r in results:
        if "error" not in r:
            groups.setdefault(r["case"], []).append(r)
    return {name: {axis: {k: float(np.mean([x[axis][k] for x in rs])) for k in KEYS} for axis in ("pan", "tilt")}
            for name, rs in groups.items()}


def check(rows: dict[str, dict], baseline: dict[str, dict] | None = None) -> list[str]:
    """The broken limits, as text."""
    bad = []
    for name, row in rows.items():
        case = Case.parse(name)
        for key, limit in {**SMOOTH_LIMITS.get(case.kind, {}), **CASE_LIMITS.get(name, {})}.items():
            if row["pan"][key] > limit:
                bad.append(f"{name}: pan {key} {row['pan'][key]:.2f} > {limit:g}")
        if not case.halting_area:
            for key, limit in NOHALT_TILT_LIMITS.items():
                if row["tilt"][key] > limit:
                    bad.append(f"{name}: tilt {key} {row['tilt'][key]:.2f} > {limit:g}")
        for i, axis in enumerate(("pan", "tilt")):
            if row[axis]["err_max"] > FAR:
                bad.append(f"{name}: {axis} err_max {row[axis]['err_max']:.1f} > {FAR:g} (far off the display position)")
            refs = [("reference", REFERENCE.get(name, (None, None))[i])]
            if baseline and name in baseline:
                refs.append(("baseline", baseline[name][axis]["err_p95"]))
            for what, ref in refs:
                limit = ref * REFERENCE_TOLERANCE[0] + REFERENCE_TOLERANCE[1] if ref is not None else None
                if limit is not None and row[axis]["err_p95"] > limit:
                    bad.append(f"{name}: {axis} err_p95 {row[axis]['err_p95']:.2f} > {limit:.2f} ({what} {ref:.2f})")
    return bad


def print_table(rows: dict[str, dict]) -> None:
    print(f"{'case':24s} axis | chg/s jmp/s strt/m rev/m stop% rippl    acc   jerk | e_rms e_p95 e_max")
    for name, row in rows.items():
        for axis in ("pan", "tilt"):
            m = row[axis]
            if axis == "tilt" and m["chg_s"] < 0.3:
                continue
            print(f"{name:24s} {axis:4s} | {m['chg_s']:5.2f} {m['jump_s']:5.2f} {m['starts_min']:6.1f} {m['rev_min']:5.1f} "
                  f"{m['stopped'] * 100:5.0f} {m['ripple']:5.2f} {m['acc_rms']:6.1f} {m['jerk_rms']:6.0f} | "
                  f"{m['err_rms']:5.2f} {m['err_p95']:5.2f} {m['err_max']:5.2f}")


def _pairs(items: list[str], what: str) -> dict:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--{what} {item!r}: key=value")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cases", nargs="*", help="e.g. walk1.2-p400-8-air (default: the standard cases)")
    p.add_argument("--slot", action="append", default=[], metavar="KEY=VALUE", help="App Settings value, e.g. smoothness=10")
    p.add_argument("--motion", action="append", default=[], metavar="KEY=VALUE", help="motion profile value, e.g. max_accel=30")
    p.add_argument("--detect-fps", type=float, default=15.0, help="detections per second (Light 15, Normal/High 30)")
    p.add_argument("--seeds", default=",".join(map(str, SEEDS)), help="e.g. 1,2,3")
    p.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="processes")
    p.add_argument("--json", help="save every run here")
    p.add_argument("--baseline", help="--json of an earlier run: framing (err_p95) may not grow beyond it x 1.25 + 0.5")
    p.add_argument("--check", action="store_true", help="exit 1 when a limit is broken")
    args = p.parse_args(argv)
    try:
        cases = [Case.parse(c) for c in args.cases] or STANDARD
    except ValueError as e:
        p.error(str(e))
    seeds = [int(s) for s in args.seeds.split(",") if s]
    results = run_cases(cases, seeds, _pairs(args.slot, "slot"), _pairs(args.motion, "motion"), args.detect_fps,
                        args.jobs)
    errors = [r for r in results if "error" in r]
    for r in errors:
        print(f"ERROR {r['case']} seed {r['seed']}:\n{r['error']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f)
    rows = summarize(results)
    print_table(rows)
    if not args.check:
        return 1 if errors else 0
    baseline = None
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as f:
            baseline = summarize(json.load(f))
    bad = check(rows, baseline)
    for b in bad:
        print("FAIL", b)
    print(f"{len(bad)} limit(s) broken" if bad else "all limits hold")
    return 1 if bad or errors else 0


if __name__ == "__main__":
    sys.exit(main())

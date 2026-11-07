"""Measure a network camera's lens and video timing from its own picture (PTZOptics G2 and other RTSP cameras).

  python tools/lens_probe.py 192.168.1.60 --yes lens     field of view by zoom position -> the zoom table
  python tools/lens_probe.py 192.168.1.60 --yes delay    video delay and the camera's reaction time

Both move the camera, so they need --yes. The camera goes back to where it was at the end (also on Ctrl+C).
Pictures are compared in memory only: nothing but the numbers is saved (lab-results/*.csv).

lens: first point the camera at something 3 m or more away with plenty of fine detail in the middle of the
picture (a poster, shelves, a plant), not at people or screens. Then it
  1. finds the zoom range (full wide to full tele),
  2. pans 6.5 degrees at full wide and measures how far the picture moves: the field of view there,
  3. zooms in step by step and measures each step's magnification from matched features (ORB, RANSAC).
It prints the zoom table for the camera type (core/camera_models.py: lens.zoom_table). A step with few matched
points is marked unsure (at full tele a near target may be out of focus): measure again on a farther one.

delay: pans the camera for a second, a few times. The video delay is the time from the camera's position readback
starting (and stopping) to change to the picture starting (stopping) to move: Settings -> Cameras -> Video delay.
The reaction is the time from the drive command to the first changed readback.

Units per degree come from `visca_probe.py HOST --transport udp calibrate --yes` (PTZOptics G2: 14.4).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import threading
import time

import av
import cv2
import numpy as np
from _common import results_path, write_csv

from trackxen.devices import visca

DEFAULT_PORTS = {"sony": visca.VISCA_PORT, "udp": visca.PLAIN_UDP_PORT, "tcp": visca.TCP_PORT}
RTSP_OPTIONS = {"fflags": "nobuffer", "flags": "low_delay", "max_delay": "0"}
WIDE_PAN = 6.5  # degrees panned at full wide
MIN_MATCHES = 40  # fewer matched points in a zoom step: its magnification is unsure


class Pictures:
    """The camera's stream, decoded on a thread: the newest picture (grey), and while recording every picture
    (small) with its arrival time."""

    def __init__(self, url: str, transport: str):
        self.latest: np.ndarray | None = None
        self.arrived = 0.0
        self.error: str | None = None
        self.stopped = False
        self._recording: list[tuple[float, np.ndarray]] | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, args=(url, transport), name="pictures", daemon=True).start()

    def _run(self, url: str, transport: str) -> None:
        try:
            with av.open(url, options={"rtsp_transport": transport, **RTSP_OPTIONS}, timeout=(10.0, 10.0)) as container:
                stream = container.streams.video[0]
                stream.thread_type = "SLICE"
                for frame in container.decode(stream):
                    if self.stopped:
                        return
                    image = frame.to_ndarray(format="gray")
                    now = time.monotonic()
                    with self._lock:
                        self.latest, self.arrived = image, now
                        if self._recording is not None:
                            self._recording.append((now, cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)))
        except Exception as e:  # noqa: BLE001  (reported by after())
            self.error = str(e) or type(e).__name__

    async def after(self, t: float, timeout: float = 10.0) -> np.ndarray:
        """The newest picture that arrived after time t."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.latest is not None and self.arrived > t:
                    return self.latest.copy()
            if self.error:
                raise RuntimeError(f"no picture: {self.error}")
            await asyncio.sleep(0.02)
        raise RuntimeError("no picture from the stream")

    def record(self) -> None:
        with self._lock:
            self._recording = []

    def recorded(self) -> list[tuple[float, np.ndarray]]:
        with self._lock:
            frames, self._recording = self._recording or [], None
        return frames


async def wait_still(read, timeout: float = 40.0, still: float = 0.6):
    """Read until the value stops changing for `still` seconds; returns it."""
    last, since, start = None, time.monotonic(), time.monotonic()
    while time.monotonic() - start < timeout:
        await asyncio.sleep(0.1)
        value = await read()
        if value != last:
            last, since = value, time.monotonic()
        elif time.monotonic() - since > still:
            return value
    raise visca.ViscaError(f"still moving after {timeout:.0f} s")


def _features(image: np.ndarray):
    return cv2.ORB_create(5000).detectAndCompute(image, None)


def _matches(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Matched feature positions in a and b."""
    (ka, da), (kb, db) = _features(a), _features(b)
    if da is None or db is None:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    return (np.float32([ka[m.queryIdx].pt for m in pairs]).reshape(-1, 2),
            np.float32([kb[m.trainIdx].pt for m in pairs]).reshape(-1, 2))


def horizontal_shift(a: np.ndarray, b: np.ndarray, keep: float = 0.5) -> tuple[float, int]:
    """How far the picture moved sideways from a to b: the median of matched features in the middle `keep` of
    the width (people walking past don't count). NaN when too few match."""
    pa, pb = _matches(a, b)
    half = a.shape[1] * keep / 2
    ok = (np.abs(pb[:, 1] - pa[:, 1]) < 6) & (np.abs(pa[:, 0] - a.shape[1] / 2) < half)
    dx = pb[ok, 0] - pa[ok, 0]
    return (float(np.median(dx)) if len(dx) >= 15 else math.nan), int(len(dx))


def magnification(wider: np.ndarray, narrower: np.ndarray) -> tuple[float, int]:
    """How much bigger things look in `narrower` than in `wider`, and the number of matched points that agree."""
    pa, pb = _matches(wider, narrower)
    if len(pa) < 20:
        return math.nan, len(pa)
    m, inliers = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if m is None:
        return math.nan, 0
    return 1 / math.hypot(m[0, 0], m[0, 1]), int(inliers.sum())


async def still_picture(pictures: Pictures, timeout: float = 8.0) -> np.ndarray:
    """A picture once the picture itself has stopped moving (it lags the camera's position readback)."""
    prev = await pictures.after(time.monotonic() + 0.5)
    deadline, calm = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        cur = await pictures.after(time.monotonic() + 0.4)
        dx, n = horizontal_shift(prev, cur, keep=0.8)
        calm = calm + 1 if n >= 15 and abs(dx) < 1.0 else 0
        if calm >= 2:
            return cur
        prev = cur
    return prev


async def zoomed_picture(pictures: Pictures, timeout: float = 8.0) -> np.ndarray:
    """A picture once zoom, focus and the lagging picture have settled (no scale change between two pictures)."""
    await asyncio.sleep(3.0)
    prev = await pictures.after(time.monotonic())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cur = await pictures.after(time.monotonic() + 0.5)
        s, n = magnification(prev, cur)
        if n >= 30 and abs(s - 1) < 0.002:
            return cur
        prev = cur
    return prev


async def zoom_to(client: visca.ViscaClient, position: int) -> int:
    await client.command(visca.zoom_direct(position))
    return await wait_still(client.zoom_position)


async def cmd_lens(client: visca.ViscaClient, pictures: Pictures, args) -> None:
    pan0, tilt0 = await client.pan_tilt_position()
    await client.command(visca.zoom_tele(7))
    zoom_max = await wait_still(client.zoom_position)
    await client.command(visca.zoom_stop())
    await zoom_to(client, 0)
    print(f"zoom range 0..{zoom_max}; pointing at pan/tilt {pan0} / {tilt0} (VISCA units)")
    step = round(WIDE_PAN * args.units_per_degree)
    wides, points = [], []
    for _ in range(3):  # each time coming from the left, so gear slack doesn't shorten the pan
        await client.command(visca.pan_tilt_absolute(pan0 - step, tilt0, 6, 6))
        await wait_still(client.pan_tilt_position)
        await client.command(visca.pan_tilt_absolute(pan0, tilt0, 6, 6))
        a_pos = await wait_still(client.pan_tilt_position)
        a = await still_picture(pictures)
        await client.command(visca.pan_tilt_absolute(pan0 + step, tilt0, 6, 6))
        b_pos = await wait_still(client.pan_tilt_position)
        b = await still_picture(pictures)
        dx, n = horizontal_shift(a, b)
        angle = (b_pos[0] - a_pos[0]) / args.units_per_degree
        hfov = 2 * math.degrees(math.atan((a.shape[1] / 2) * math.tan(math.radians(abs(angle))) / abs(dx)))
        wides.append(hfov)
        points.append(n)
        print(f"full wide: pan {angle:.3f} deg moves the picture {dx:+.1f} px ({n} points): {hfov:.2f} deg")
    if not any(math.isfinite(w) for w in wides):
        raise visca.ViscaError("the picture's movement couldn't be measured: point the camera at more detail")
    wide = float(np.nanmedian(wides))
    await client.command(visca.pan_tilt_absolute(pan0, tilt0, 6, 6))
    await wait_still(client.pan_tilt_position)
    rows, half_tan, unsure = [[0, round(wide, 2), int(np.median(points)), ""]], math.tan(math.radians(wide / 2)), []
    prev = await zoomed_picture(pictures)
    for k in range(1, args.steps + 1):
        z = await zoom_to(client, round(zoom_max * k / args.steps))
        cur = await zoomed_picture(pictures)
        s, n = magnification(prev, cur)
        if not math.isfinite(s):  # each step builds on the last: the table ends here
            print(f"zoom {z:6d}: no match ({n} points): the table stops at zoom {rows[-1][0]}. At full tele a near "
                  "target is out of focus: aim at a farther, more detailed one to measure the rest")
            break
        half_tan /= s
        hfov = 2 * math.degrees(math.atan(half_tan))
        if n < MIN_MATCHES:
            unsure.append(z)
        rows.append([z, round(hfov, 2), n, round(s, 4)])
        print(f"zoom {z:6d}: x{s:.3f} ({n} points{', unsure' if n < MIN_MATCHES else ''}): {hfov:.2f} deg")
        prev = cur
    ratio = math.tan(math.radians(wide / 2)) / half_tan
    print(f"\nfull wide {wide:.2f} deg, {rows[-1][1]:.2f} deg at zoom {rows[-1][0]} (of {zoom_max}): {ratio:.1f}x")
    if unsure:
        print(f"unsure at zoom {', '.join(map(str, unsure))}: few matching points; aim at a farther, more detailed target")
    table = ", ".join(f"[{z}, {h}]" for z, h, *_ in rows)
    print(f'"lens": {{"hfov_wide": {rows[0][1]}, "hfov_tele": {rows[-1][1]}, "zoom_max": {float(rows[-1][0])},')
    print(f'         "zoom_table": [{table}]}},')
    if rows[-1][0] < zoom_max:
        print(f"(the camera zooms on to {zoom_max}: measure the rest before using the table, or TrackXen won't zoom past "
              f"{rows[-1][0]})")
    path = results_path("lens")
    write_csv(path, ["zoom", "hfov_deg", "matched_points", "step_magnification"], rows)
    print(f"saved {path}")


def picture_shift(a: np.ndarray, b: np.ndarray) -> float:
    """Sideways shift between two small pictures (phase correlation)."""
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, _dy), _response = cv2.phaseCorrelate(np.float32(a), np.float32(b), window)
    return dx


async def cmd_delay(client: visca.ViscaClient, pictures: Pictures, args) -> None:
    await pictures.after(0.0)
    starts, stops, reactions, rows = [], [], [], []
    for k in range(args.runs):
        await asyncio.sleep(3.0)  # everything still, also the lagging picture
        pictures.record()
        readback: list[tuple[float, int]] = []
        direction = visca.PanDir.RIGHT if k % 2 == 0 else visca.PanDir.LEFT
        t_cmd = time.monotonic()
        await client.command(visca.pan_tilt_drive(args.speed, 1, direction, visca.TiltDir.STOP))
        stop_sent = False
        while time.monotonic() < t_cmd + args.seconds + 4.0:
            readback.append((time.monotonic(), (await client.pan_tilt_position())[0]))
            if not stop_sent and time.monotonic() >= t_cmd + args.seconds:
                await client.command(visca.pan_tilt_stop())
                stop_sent = True
            await asyncio.sleep(0.04)
        frames = pictures.recorded()
        first, last = readback[0][1], readback[-1][1]
        moved = next((t for t, p in readback if p != first), None)
        settled = next((t for t, p in readback if moved and t > moved and p == last), None)
        offsets = [(t, picture_shift(frames[0][1], f)) for t, f in frames]  # the picture against the first one
        final = float(np.median([dx for _, dx in offsets[-5:]]))
        pic_moved = next((t for t, dx in offsets if t > t_cmd and abs(dx) > 3.0), None)
        pic_settled = next((t for t, dx in offsets if pic_moved and t > pic_moved and abs(dx - final) < 1.5), None)
        ms = lambda a, b: round((a - b) * 1000) if a is not None and b is not None else None  # noqa: E731
        row = [k + 1, ms(moved, t_cmd), ms(pic_moved, moved), ms(pic_settled, settled), len(frames), round(final, 1)]
        rows.append(row)
        reactions += [row[1]] if row[1] is not None else []
        starts += [row[2]] if row[2] is not None else []
        stops += [row[3]] if row[3] is not None else []
        print(f"run {k + 1}: the camera reacts after {row[1]} ms; the picture starts {row[2]} ms and stops {row[3]} ms "
              f"after the readback ({len(frames)} pictures, moved {final:+.0f} px of 640)")
    await client.command(visca.pan_tilt_stop())
    if starts or stops:
        delay = float(np.median(starts + stops)) / 1000
        print(f"\nvideo delay: median {delay:.3f} s (start {np.median(starts) if starts else '-'} ms, "
              f"end {np.median(stops) if stops else '-'} ms) -> Settings -> Cameras -> Video delay")
    if reactions:
        print(f"the camera reacts to a command after {np.median(reactions):.0f} ms (median)")
    path = results_path("delay")
    write_csv(path, ["run", "reaction_ms", "picture_start_ms", "picture_stop_ms", "pictures", "moved_px"], rows)
    print(f"saved {path}")


async def main(args) -> int:
    if not args.yes:
        print("This moves the camera. Add --yes to confirm it's safe to move it now (not while it is live).")
        return 2
    client = visca.open_client(args.host, args.port or DEFAULT_PORTS[args.transport], args.transport)
    await client.connect()
    pictures = Pictures(args.stream or f"rtsp://{args.host}:554/1", args.rtsp_transport)
    pan0 = tilt0 = zoom0 = None
    try:
        pan0, tilt0 = await client.pan_tilt_position()
        zoom0 = await client.zoom_position()
        await pictures.after(0.0)
        await (cmd_lens if args.command == "lens" else cmd_delay)(client, pictures, args)
        return 0
    except (visca.ViscaError, RuntimeError) as e:
        print(f"error: {e}")
        return 1
    finally:
        pictures.stopped = True
        try:
            await client.stop_all()
            if pan0 is not None:  # back to where it was
                await client.command(visca.pan_tilt_absolute(pan0, tilt0, 12, 12))
                await client.command(visca.zoom_direct(zoom0))
        except visca.ViscaError:
            pass
        client.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--transport", choices=["sony", "udp", "tcp"], default="udp",
                   help="VISCA: udp / tcp = plain VISCA (PTZOptics 1259 / 5678), sony = with header (UDP 52381)")
    p.add_argument("--port", type=int, help="VISCA port (default by --transport)")
    p.add_argument("--stream", help="RTSP address (default rtsp://HOST:554/1, the main stream)")
    p.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    p.add_argument("--yes", action="store_true", help="confirm that the camera may move")
    sub = p.add_subparsers(dest="command", required=True)
    lens = sub.add_parser("lens", help="field of view by zoom position (the zoom table)")
    lens.add_argument("--units-per-degree", type=float, default=14.4, help="pan, from visca_probe.py calibrate")
    lens.add_argument("--steps", type=int, default=8, help="zoom steps from full wide to full tele")
    delay = sub.add_parser("delay", help="video delay and the camera's reaction time")
    delay.add_argument("--runs", type=int, default=4)
    delay.add_argument("--speed", type=int, default=4, help="pan speed step")
    delay.add_argument("--seconds", type=float, default=1.0, help="pan time per run")
    for s in (lens, delay):  # --yes also after the command
        s.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="confirm that the camera may move")
    return p


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(build_parser().parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)

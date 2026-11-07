"""Measure a camera's own network stream (RTSP), e.g. a PTZOptics G2's rtsp://IP:554/1.

  python tools/stream_probe.py rtsp://192.168.1.60:554/1
  python tools/stream_probe.py rtsp://192.168.1.60:554/2 --transport udp --seconds 30
  python tools/stream_probe.py rtsp://192.168.1.60:554/1 --delay 192.168.1.60 --yes   (the camera pans a little)

Reports what was measured: picture size and codec, frame rate, gaps between frames, decode time
per frame, damaged frames, time to the first picture. With --delay it also measures the video
delay to put into Settings -> Cameras -> Video delay: the camera pans (plain VISCA on UDP 1259 by
default), and the time from when its reported position starts changing to when the picture
starts moving is the delay, repeated --repeats times (median taken).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import cv2
import numpy as np
from _common import results_path, write_csv

from trackxen.devices import visca
from trackxen.vision.sources import StreamSource


def pct(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * q))] if values else float("nan")


def measure_stream(src: StreamSource, seconds: float) -> dict:
    t0 = time.perf_counter()
    while src.frames_received == 0 and time.perf_counter() - t0 < 15:
        if src.last_error and time.perf_counter() - t0 > 8:
            break
        time.sleep(0.01)
    first = time.perf_counter() - t0 if src.frames_received else None
    arrivals, decode, start, seen = [], [], time.perf_counter(), src.frames_received
    while time.perf_counter() - start < seconds:
        frame = src.latest()
        if frame and frame.number != seen:
            seen = frame.number
            arrivals.append(frame.measured)
        time.sleep(0.002)
    decode = list(src.decode_times)
    gaps = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
    return {"first_picture_s": round(first, 2) if first else None, "width": src.size[0], "height": src.size[1],
            "codec": src.codec, "frames": len(arrivals),
            "fps": round((len(arrivals) - 1) / (arrivals[-1] - arrivals[0]), 2) if len(arrivals) > 2 else 0.0,
            "gap_ms_p50": round(statistics.median(gaps), 1) if gaps else None, "gap_ms_p95": round(pct(gaps, 0.95), 1),
            "gap_ms_max": round(max(gaps), 1) if gaps else None,
            "decode_ms_p50": round(statistics.median(decode), 2) if decode else None,
            "decode_ms_p95": round(pct(decode, 0.95), 2), "damaged": src.ndi_frames_dropped,
            "error": src.last_error}


def motion(a: np.ndarray, b: np.ndarray) -> float:
    """How much the picture changed (mean absolute difference of small grey copies)."""
    small = [cv2.resize(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.int16) for x in (a, b)]
    return float(np.mean(np.abs(small[0] - small[1])))


async def one_delay(src: StreamSource, client: visca.ViscaClient, direction: visca.PanDir, speed: int) -> float | None:
    """Pan from standstill: the time from the first changed position reply to the first changed picture."""
    await asyncio.sleep(1.0)
    frame = src.latest()
    if frame is None:
        return None
    base, noise, t_still = frame.image, 0.0, time.perf_counter() + 0.5
    while time.perf_counter() < t_still:  # standing still: how much the picture changes by itself (noise)
        noise = max(noise, motion(base, src.latest().image))
        await asyncio.sleep(0.02)
    p0 = await client.pan_tilt_position()
    await client.command(visca.pan_tilt_drive(speed, 1, direction, visca.TiltDir.STOP))
    moved_at = seen_at = None
    t_end = time.perf_counter() + 3.0
    while time.perf_counter() < t_end and (moved_at is None or seen_at is None):
        if moved_at is None:
            t_ask = time.perf_counter()
            if (await client.pan_tilt_position()) != p0:
                moved_at = (t_ask + time.perf_counter()) / 2
        frame = src.latest()
        if seen_at is None and frame is not None and motion(base, frame.image) > noise * 2 + 1.5:
            seen_at = frame.measured
        await asyncio.sleep(0.002)
    await client.command(visca.pan_tilt_stop())
    return (seen_at - moved_at) if moved_at and seen_at else None


async def measure_delay(src: StreamSource, args) -> list[float]:
    client = visca.open_client(args.delay, args.visca_port, args.visca_transport, timeout=1.0)
    await client.connect()
    delays = []
    try:
        for i in range(args.repeats):
            d = await one_delay(src, client, visca.PanDir.RIGHT if i % 2 == 0 else visca.PanDir.LEFT, args.speed)
            print(f"  delay {i + 1}: {'-' if d is None else f'{d * 1000:.0f} ms'}", flush=True)
            if d is not None:
                delays.append(d)
    finally:
        await client.stop_all()
        client.close()
    return delays


def main(args) -> int:
    if args.delay and not args.yes:
        print("--delay pans the camera a little. Add --yes to confirm it's safe to move it now.")
        return 2
    src = StreamSource(args.url, args.transport)
    src.start()
    try:
        r = measure_stream(src, args.seconds)
        if not r["frames"]:
            print(f"no picture: {r['error'] or 'nothing arrived'}")
            return 1
        print(f"{src.source_name}: {r['width']}x{r['height']} {r['codec']}, first picture after {r['first_picture_s']} s")
        print(f"  {r['fps']} fps; gaps p50 {r['gap_ms_p50']} ms, p95 {r['gap_ms_p95']} ms, max {r['gap_ms_max']} ms")
        print(f"  decode {r['decode_ms_p50']} ms per frame (p95 {r['decode_ms_p95']} ms); damaged frames {r['damaged']}")
        if args.delay:
            delays = asyncio.run(measure_delay(src, args))
            r["video_delay_s"] = round(statistics.median(delays), 3) if delays else None
            print(f"video delay (median of {len(delays)}): "
                  + (f"{r['video_delay_s']:.3f} s  -> Settings -> Cameras -> Video delay" if delays else "not measured"))
    finally:
        src.stop()
    path = results_path("stream-probe")
    write_csv(path, list(r), [list(r.values())])
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="rtsp://IP:554/1 (PTZOptics main stream) or /2 (sub stream)")
    p.add_argument("--transport", choices=["tcp", "udp"], default="tcp", help="how the RTSP packets travel")
    p.add_argument("--seconds", type=float, default=15.0)
    p.add_argument("--delay", metavar="CAMERA_IP", help="also measure the video delay (pans the camera)")
    p.add_argument("--visca-transport", choices=["sony", "udp", "tcp"], default="udp")
    p.add_argument("--visca-port", type=int, default=visca.PLAIN_UDP_PORT)
    p.add_argument("--speed", type=int, default=6, help="pan speed step for the delay test")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--yes", action="store_true", help="confirm that the camera may move")
    try:
        sys.exit(main(p.parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)

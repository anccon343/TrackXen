from trackxen.vision.worker import VisionLoop


def limiter(fps: float) -> VisionLoop:
    loop = VisionLoop.__new__(VisionLoop)  # just the rate limit: no sources or model
    loop.max_detect_fps, loop._last_detect, loop._tokens = fps, {}, {}
    return loop


def arrivals(gaps_ms: list[float], repeat: int) -> list[float]:
    t, out = 0.0, []
    for _ in range(repeat):
        for g in gaps_ms:
            t += g / 1000
            out.append(t)
    return out


def test_limit_at_the_source_rate_takes_every_jittery_frame():
    loop = limiter(30)
    times = arrivals([31, 47, 22], 30)  # 30 fps on average, unevenly spaced
    assert all(loop._allowed("cam1", t) for t in times)


def test_lower_limit_takes_every_other_frame_without_delay():
    loop = limiter(15)
    times = arrivals([33.3], 90)  # 3 s at 30 fps
    taken = [t for t in times if loop._allowed("cam1", t)]
    assert abs(len(taken) / 3 - 15) <= 1  # ~15 per second
    assert max(b - a for a, b in zip(taken, taken[1:])) < 0.07  # never two skipped in a row


def test_cameras_have_their_own_budget():
    loop = limiter(30)
    times = arrivals([33.3], 30)
    for t in times:
        assert loop._allowed("cam1", t) and loop._allowed("cam2", t + 0.005)


def test_video_sources_by_kind():
    from trackxen.devices.ndi_in import NdiReceiver
    from trackxen.vision.sources import CaptureSource, FileSource, SourceConfig, open_source

    assert isinstance(open_source(SourceConfig("cam1", "ndi", "BIRDDOG")), NdiReceiver)
    capture = open_source(SourceConfig("cam2", "capture", "3"))
    assert isinstance(capture, CaptureSource) and capture.index == 3 and capture.source_name == "capture device 3"
    assert type(open_source(SourceConfig("cam3", "file", "x.mp4"))) is FileSource


def test_weaker_keep_tracking_setting_lowers_the_detector_threshold(tmp_path):
    import numpy as np

    from test_yolo_pose import synthetic_model
    from trackxen.vision.sources import SourceConfig
    from trackxen.vision.worker import WorkerConfig
    from trackxen.vision.yolo_pose import Batch, Letterbox

    synthetic_model(tmp_path / "synthetic-b1.onnx")
    cfg = WorkerConfig(sources=[SourceConfig("cam1", "file", str(tmp_path / "none.mp4"))], model_dir=str(tmp_path),
                       model_name="synthetic", engines=("cpu",), pipeline=False, tracker={"low_score": 0.05})
    loop = VisionLoop(cfg, lambda msg: None)
    try:
        out = np.zeros((1, 300, 57), np.float32)
        out[0, 0, :6] = [100, 100, 200, 400, 0.08, 0]  # someone the detector is only 8% sure of
        batch = Batch(out, (Letterbox(1.0, 0, 0),), 1, 0.0)
        assert len(loop.detectors.get().decode(batch, out)[0]) == 1  # "Keep tracking down to" 0.05
        loop.command({"type": "tracker", "settings": {"low_score": 0.1}})  # changed live
        assert loop.detectors.get().conf == 0.1 and loop.detectors.get().decode(batch, out)[0] == []
        loop.command({"type": "tracker", "settings": {"low_score": 0.4, "high_score": 0.06}})
        assert loop.detectors.get().conf == 0.06  # sure detections count even below "keep"
    finally:
        loop.close()


def stream_error(source) -> str:
    """Start a network stream source and return the first error it reports."""
    import time

    source.start()
    try:
        deadline = time.monotonic() + 10.0
        while not source.last_error:
            assert time.monotonic() < deadline, "no error reported"
            time.sleep(0.02)
        time.sleep(0.1)
        assert source._thread.is_alive()  # still trying: it reconnects
        return source.last_error
    finally:
        source.stop()


def test_stream_without_video_is_reported_and_retried(tmp_path):
    import wave

    from trackxen.vision.sources import StreamSource

    path = tmp_path / "audio.wav"  # opens fine, but has no picture
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    assert "no video" in stream_error(StreamSource(str(path), loop=True))


def test_stream_errors_never_show_the_password():
    import socket

    from trackxen.vision.sources import StreamSource

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    source = StreamSource(f"rtsp://admin:secret@127.0.0.1:{port}/1", timeout=2.0)
    assert source.source_name == f"rtsp://127.0.0.1:{port}/1"
    error = stream_error(source)
    assert f"127.0.0.1:{port}" in error and "secret" not in error and "admin" not in error

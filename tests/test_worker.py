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

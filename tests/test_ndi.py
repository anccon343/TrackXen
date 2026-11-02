"""NDI input: discovery must not pause the other threads, and partial names must not pick the wrong camera."""

import threading
import time

import pytest

pytest.importorskip("cyndilib")

from trackxen.devices import ndi_in  # noqa: E402


def longest_pause(fn) -> tuple[float, object]:
    """Run fn() while another thread ticks every 2 ms; the longest gap between its ticks (seconds)."""
    gaps, stop = [0.0], threading.Event()

    def tick():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    thread = threading.Thread(target=tick)
    thread.start()
    time.sleep(0.05)
    try:
        result = fn()
    except LookupError as e:
        result = e
    finally:
        stop.set()
        thread.join()
    return max(gaps), result


def test_finding_sources_lets_other_threads_run():
    """cyndilib's wait_for_sources holds the GIL: a 3 s search froze the camera control loop for 3 s."""
    pause, sources = longest_pause(lambda: ndi_in.find_sources(0.6))
    assert isinstance(sources, list)
    assert pause < 0.2


def test_a_missing_source_lets_other_threads_run_and_a_retry_returns_at_once():
    receiver = ndi_in.NdiReceiver("TRACKXEN TEST NOBODY SENDS THIS")
    try:
        pause, error = longest_pause(lambda: receiver.start(timeout=0.6))
        assert isinstance(error, LookupError) and "not found" in str(error)
        assert pause < 0.2  # (each 0.5 s wait paused every thread of the vision worker)
        t0 = time.monotonic()
        with pytest.raises(LookupError):  # the worker retries on its detection thread
            receiver.start(timeout=0.6)
        assert time.monotonic() - t0 < 0.2
    finally:
        receiver.stop()


class StubFinder:
    def __init__(self, names: list[str]):
        self.names, self.is_open = names, False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def update_sources(self):
        return self.names

    def get_source_names(self):
        return list(self.names)

    def get_source(self, name):
        return name if name in self.names else None


def found(wanted: str, names: list[str]):
    receiver = ndi_in.NdiReceiver(wanted)
    receiver._finder = StubFinder(names)
    return receiver._find()


def test_partial_names_find_only_one_source():
    assert found("TRACKXEN SIM 1", ["ANTTONPC (TRACKXEN SIM 1)", "ANTTONPC (TRACKXEN SIM 2)"]) == "ANTTONPC (TRACKXEN SIM 1)"
    assert found("BIRDDOG-12345", ["BIRDDOG-12345 (CAM)"]) == "BIRDDOG-12345 (CAM)"
    assert found("ALTAR (CAM)", ["SIDEALTAR (CAM)", "ALTAR (CAM)"]) == "ALTAR (CAM)"
    assert found("altar (cam)", ["SIDEALTAR (CAM)", "ALTAR (CAM)"]) == "ALTAR (CAM)"
    assert found("CAM1", ["PC (CAM10)", "PC (CAM1)"]) == "PC (CAM1)"  # a whole stream name wins


def test_a_full_name_never_takes_another_source_that_contains_it():
    """The camera's own source is not announced yet: waiting is right, tracking another camera's video is not."""
    assert found("ALTAR (CAM)", ["SIDEALTAR (CAM)"]) is None


def test_an_ambiguous_partial_name_is_reported_not_guessed():
    assert found("BIRDDOG", ["BIRDDOG-1 (CAM)", "BIRDDOG-2 (CAM)"]) is None
    receiver = ndi_in.NdiReceiver("BIRDDOG")
    receiver._finder = StubFinder(["BIRDDOG-1 (CAM)", "BIRDDOG-2 (CAM)"])
    with pytest.raises(LookupError, match="several sources"):
        receiver.start(timeout=0.1)
    assert receiver.source_name == "BIRDDOG"


class AnnouncingFinder(StubFinder):
    """Sources announced one by one: (seconds after the finder opened, name)."""

    def __init__(self, schedule: list[tuple[float, str]]):
        super().__init__([])
        self.schedule, self.opened = schedule, 0.0

    def open(self):
        super().open()
        self.opened = time.monotonic()

    def update_sources(self):
        self.names = [name for after, name in self.schedule if time.monotonic() - self.opened >= after]
        return self.names


class FakeReceiver:
    def __init__(self, **kwargs):
        self.source = None
        self.frame_sync = self

    def set_video_frame(self, video):
        pass

    def set_source(self, source):
        self.source = source

    def disconnect(self):
        pass


def started(wanted: str, schedule: list[tuple[float, str]], monkeypatch, timeout: float = 1.0) -> str:
    """The source start() connects to, with the sources announced as in `schedule`."""
    monkeypatch.setattr(ndi_in, "Receiver", FakeReceiver)
    receiver = ndi_in.NdiReceiver(wanted)
    receiver._finder = AnnouncingFinder(schedule)
    receiver._run = lambda: None
    receiver.start(timeout=timeout)
    return receiver.receiver.source


def test_a_partial_name_waits_for_its_own_source(monkeypatch):
    """"CAM1" while only "PC (CAM10)" is announced (camera 1 still booting): it contains "CAM1", but not as its
    whole host or stream name. Taking it would track camera 10's video with camera 1."""
    assert started("CAM1", [(0.0, "PC (CAM10)"), (0.3, "PC (CAM1)")], monkeypatch) == "PC (CAM1)"
    assert started("BIRDDOG-12345", [(0.0, "BIRDDOG-12345 (CAM)")], monkeypatch) == "BIRDDOG-12345 (CAM)"


def test_a_partial_name_inside_a_name_is_taken_once_the_search_is_over(monkeypatch):
    t0 = time.monotonic()
    assert started("BIRDDOG", [(0.0, "BIRDDOG-1 (CAM)")], monkeypatch, timeout=0.3) == "BIRDDOG-1 (CAM)"
    assert time.monotonic() - t0 >= 0.3  # (after waiting for a source with exactly that name)

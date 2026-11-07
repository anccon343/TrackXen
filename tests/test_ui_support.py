"""Engine behaviour the web UI (ui/src) relies on."""

from trackxen.core.config import CameraConfig
from trackxen.core.server import tail_lines
from trackxen.core.session import CameraSession

# --- settings_rev: pages reload a camera's App Settings when it goes up (ui/src/lib/slots.svelte.ts) ---


def test_a_new_session_counts_on_from_above_the_one_it_replaces():
    """Applying settings or loading a show file makes new camera sessions: a count starting again from
    1 left every page showing the old App Settings."""
    old = CameraSession(CameraConfig(id="cam1"), link=None)
    for _ in range(20):  # settings changed on the tracking screen
        old.update_slot({"sensitivity": 3})
    new = CameraSession(CameraConfig(id="cam1"), link=None)  # settings applied: the camera restarts
    assert new.settings_rev > old.settings_rev


def test_settings_rev_goes_up_with_each_change():
    s = CameraSession(CameraConfig(id="cam1"), link=None)
    rev = s.settings_rev
    s.update_slot({"sensitivity": 4})
    assert s.settings_rev > rev
    s.settings_rev += 1  # (how the app and the face manager bump it)
    assert s.settings_rev > rev + 1


# --- GET /api/log: the Statistics page asks for the last 200 lines every 3 s -------------------------


def _log(tmp_path, lines: list[str]):
    path = tmp_path / "TrackXen.log"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_tail_of_a_big_log_is_its_last_lines(tmp_path):
    lines = [f"2026-11-02 10:{i // 60 % 60:02d}:{i % 60:02d}  INFO    cam1   event {i} ää" for i in range(60000)]
    path = _log(tmp_path, lines)
    assert path.stat().st_size > 3_000_000
    assert tail_lines(path, 200) == "\n".join(lines[-200:])
    assert tail_lines(path, 5000) == "\n".join(lines[-5000:])  # more than the first piece read


def test_tail_of_a_short_log_is_all_of_it(tmp_path):
    path = _log(tmp_path, ["one", "two", "three"])
    assert tail_lines(path, 200) == "one\ntwo\nthree"
    assert tail_lines(path, 2) == "two\nthree"
    assert tail_lines(_log(tmp_path, []), 200) == ""

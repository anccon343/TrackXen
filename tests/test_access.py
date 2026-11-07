import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trackxen.core import access
from trackxen.core.access import COOKIE, AccessGuard
from trackxen.core.server import create_server

TABLET = ("192.168.1.20", 50000)
THIS_PC = ("127.0.0.1", 50000)
PC = "http://192.168.1.5:8090"  # how tablets open TrackXen (this PC itself: http://127.0.0.1:8090)


@pytest.fixture(autouse=True)
def quick_hash(monkeypatch):
    monkeypatch.setattr(access, "ITERATIONS", 1000)  # the real 200 000 rounds take ~0.1 s per check


class StubApp:
    """Just what the routes used here need."""

    listeners: set = set()
    previews: dict = {}

    def status(self):
        return {"cameras": []}

    async def command(self, cmd):
        return {"ok": True}


def server(tmp_path, client=TABLET) -> tuple[TestClient, AccessGuard]:
    guard = AccessGuard(tmp_path / "access.json")
    base = "http://127.0.0.1:8090" if client == THIS_PC else PC
    return TestClient(create_server(StubApp(), guard), base_url=base, client=client), guard


def test_pin_is_stored_hashed_and_survives_a_restart(tmp_path):
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("4321")
    text = (tmp_path / "access.json").read_text()
    assert "4321" not in text and json.loads(text)["pin"]["hash"]
    again = AccessGuard(tmp_path / "access.json")
    assert again.enabled and again.check_pin("10.0.0.5", "4321") and not again.check_pin("10.0.0.5", "1111")


def test_pin_must_be_4_to_8_digits(tmp_path):
    guard = AccessGuard(tmp_path / "access.json")
    for bad in ("123", "123456789", "12a4", ""):
        with pytest.raises(ValueError):
            guard.set_pin(bad)
    assert not guard.enabled


def test_wrong_pins_make_an_address_wait_longer_each_time(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(access.time, "monotonic", lambda: clock[0])
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("2468")
    for _ in range(access.FREE_TRIES - 1):
        assert not guard.check_pin("10.0.0.9", "0000")
    assert guard.wait_left("10.0.0.9") == 0
    assert not guard.check_pin("10.0.0.9", "0000")  # the fifth: now it waits
    assert guard.wait_left("10.0.0.9") == access.FIRST_WAIT
    assert not guard.check_pin("10.0.0.9", "2468")  # even the right PIN waits
    assert guard.check_pin("10.0.0.10", "2468")  # another device isn't affected
    clock[0] += access.FIRST_WAIT
    assert not guard.check_pin("10.0.0.9", "0000")
    assert guard.wait_left("10.0.0.9") == 2 * access.FIRST_WAIT
    clock[0] += 2 * access.FIRST_WAIT
    assert guard.check_pin("10.0.0.9", "2468") and guard.wait_left("10.0.0.9") == 0


def test_without_a_pin_everything_is_open(tmp_path):
    client, _ = server(tmp_path)
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/access").json()["needs_pin"] is False


def test_with_a_pin_a_tablet_signs_in_once(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("1357")
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/command", json={"type": "stop_all"}).status_code == 401
    state = client.get("/api/access").json()
    assert state["enabled"] and state["needs_pin"] and not state["local"]
    assert client.post("/api/access/login", json={"pin": "0000"}).status_code == 401
    r = client.post("/api/access/login", json={"pin": "1357"})
    assert r.status_code == 200 and COOKIE in r.cookies
    assert client.get("/api/status").status_code == 200  # the cookie does it from now on
    assert client.get("/api/access").json()["signed_in"]
    client.post("/api/access/logout")
    assert client.get("/api/status").status_code == 401


def test_too_many_wrong_pins_answer_429(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("1357")
    codes = [client.post("/api/access/login", json={"pin": "9999"}).status_code for _ in range(access.FREE_TRIES)]
    assert codes == [401] * (access.FREE_TRIES - 1) + [429]
    assert client.post("/api/access/login", json={"pin": "1357"}).status_code == 429


def test_companion_sends_the_pin_in_a_header(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("8642")
    assert client.post("/api/command", json={"type": "stop_all"}, headers={"X-TrackXen-PIN": "8642"}).status_code == 200
    assert client.post("/api/command", json={"type": "stop_all"}, headers={"X-TrackXen-PIN": "1111"}).status_code == 401


def test_this_pc_needs_no_pin_unless_asked(tmp_path):
    client, guard = server(tmp_path, client=THIS_PC)
    guard.set_pin("1234")
    assert client.get("/api/status").status_code == 200
    guard.set_ask_local(True)
    assert client.get("/api/status").status_code == 401


def test_changing_the_pin_from_a_tablet_needs_the_current_one(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("1234")
    client.post("/api/access/login", json={"pin": "1234"})
    assert client.post("/api/access/pin", json={"pin": "5678", "current": "0000"}).status_code == 403
    r = client.post("/api/access/pin", json={"pin": "5678", "current": "1234"})
    assert r.status_code == 200 and r.json()["signed_in"]  # the changing tablet stays signed in...
    other = TestClient(client.app, base_url=PC, client=("192.168.1.30", 50000))
    assert other.post("/api/access/login", json={"pin": "1234"}).status_code == 401  # ...the old PIN is gone
    assert client.post("/api/access/pin", json={"pin": None, "current": "5678"}).status_code == 200
    assert not guard.enabled and other.get("/api/status").status_code == 200


def test_the_live_connection_needs_the_pin_too(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("1234")
    with client.websocket_connect("ws://192.168.1.5:8090/ws") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    assert closed.value.code == 4401
    client.post("/api/access/login", json={"pin": "1234"})
    with client.websocket_connect("ws://192.168.1.5:8090/ws") as ws:
        assert ws.receive_json()["type"] == "status"


def test_the_page_itself_loads_without_a_pin(tmp_path):
    client, guard = server(tmp_path)
    guard.set_pin("1234")
    assert client.get("/").status_code == 200


def test_pins_sent_all_at_once_still_wait_after_five(tmp_path, monkeypatch):
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("2468")
    real, hashed = access.hashlib.pbkdf2_hmac, []

    def slow(*args):
        hashed.append(args)
        time.sleep(0.05)  # the real 200 000 rounds take ~0.1 s, and other requests run meanwhile
        return real(*args)

    monkeypatch.setattr(access.hashlib, "pbkdf2_hmac", slow)
    with ThreadPoolExecutor(20) as pool:  # like the server's thread pool
        results = list(pool.map(lambda _: guard.check_pin("10.0.0.9", "0000"), range(20)))
    assert not any(results) and len(hashed) == access.FREE_TRIES
    assert guard.wait_left("10.0.0.9") == pytest.approx(access.FIRST_WAIT, abs=1)


def test_devices_signing_in_at_once_are_all_kept(tmp_path):
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("2468")
    for _ in range(5):
        with ThreadPoolExecutor(16) as pool:
            tokens = list(pool.map(lambda i: guard.sign_in(f"10.0.0.{i}", "2468"), range(16)))
        again = AccessGuard(tmp_path / "access.json")
        assert all(tokens) and all(again.signed_in(t) for t in tokens)


def test_a_tool_sending_the_right_pin_in_a_burst_is_never_refused(tmp_path, monkeypatch):
    """A Companion button with several actions: 12 requests at once with the correct header PIN, and none
    remembered yet. Each try is counted before the slow comparison, so they must not count each other as
    the five wrong ones (6th and later refused with 401, STOP ALL among them)."""
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("2468")
    real, hashed = access.hashlib.pbkdf2_hmac, []

    def slow(*args):
        hashed.append(args)
        time.sleep(0.05)
        return real(*args)

    monkeypatch.setattr(access.hashlib, "pbkdf2_hmac", slow)
    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(lambda _: guard.allowed("10.0.0.9", None, "2468"), range(12)))
    assert all(results) and len(hashed) == 1  # (the others wait for the first check and use its answer)
    assert guard.wait_left("10.0.0.9") == 0


def test_wrong_header_pins_sent_all_at_once_still_wait_after_five(tmp_path, monkeypatch):
    guard = AccessGuard(tmp_path / "access.json")
    guard.set_pin("2468")
    real, hashed = access.hashlib.pbkdf2_hmac, []

    def slow(*args):
        hashed.append(args)
        time.sleep(0.02)
        return real(*args)

    monkeypatch.setattr(access.hashlib, "pbkdf2_hmac", slow)
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda _: guard.allowed("10.0.0.9", None, "0000"), range(20)))
    assert not any(results) and len(hashed) == access.FREE_TRIES
    assert guard.wait_left("10.0.0.9") == pytest.approx(access.FIRST_WAIT, abs=1)
    assert not guard.allowed("10.0.0.9", None, "2468")  # the right PIN waits too

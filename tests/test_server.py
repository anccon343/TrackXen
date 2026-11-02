import asyncio
import json
import socket
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trackxen.core import access
from trackxen.core.access import AccessGuard
from trackxen.core.config import AppConfig, CameraConfig, FaceRef
from trackxen.core.faces import FaceManager
from trackxen.core.server import SIGNED_OUT, create_server
from trackxen.rules.audio import MicMapping

PC = "http://192.168.1.5:8090"  # tablets open TrackXen by the PC's IP address
TABLET = ("192.168.1.20", 50000)
THIS_PC = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def quick_hash(monkeypatch):
    monkeypatch.setattr(access, "ITERATIONS", 1000)


class Log:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []

    def event(self, kind, message, level="info", source=""):
        self.events.append((kind, message, level))


class Commands:
    def __init__(self):
        self.sent: list[dict] = []

    def put(self, msg):
        self.sent.append(msg)


class StubApp:
    """Just what the routes and the face manager use."""

    def __init__(self, data):
        self.data = data
        self.config = AppConfig(cameras=[CameraConfig(id="cam1")])
        self.config_path = data / "settings.json"
        self.config_rev = 0
        self.listeners: set = set()
        self.previews: dict = {}
        self.sessions: dict = {}
        self.commands = Commands()
        self.worker = (None, self.commands, None)
        self.log = Log()
        self.faces = FaceManager(self)
        self.applied: list[tuple[AppConfig, bool]] = []
        self.ran: list[dict] = []
        self.discovered = 0
        self.status_calls = 0
        self.status_fails_after: int | None = None

    def status(self):
        self.status_calls += 1
        if self.status_fails_after is not None and self.status_calls > self.status_fails_after:
            raise ZeroDivisionError("float division by zero")
        return {"cameras": []}

    async def command(self, cmd):
        self.ran.append(cmd)
        return {"ok": True}

    async def apply_config(self, config, whole=False):
        self.applied.append((config, whole))
        self.config = config
        self.config_rev += 1

    async def discover(self):
        self.discovered += 1
        return {"ndi": [], "mixers": []}


def server(tmp_path, base_url=PC, client=TABLET):
    app = StubApp(tmp_path)
    guard = AccessGuard(tmp_path / "access.json")
    return TestClient(create_server(app, guard), base_url=base_url, client=client), app, guard


def live(client, **kwargs):
    """The live connection, to the host the client uses (TestClient's own would say "testserver")."""
    return client.websocket_connect(f"ws://{client.base_url.netloc.decode()}/ws", **kwargs)


def session(camera="cam1", faces=()):
    return SimpleNamespace(cfg=CameraConfig(id=camera, faces=[FaceRef(id=f) for f in faces]), settings_rev=0)


# --- another web site's page --------------------------------------------------------------------


def test_a_page_renamed_to_this_pc_is_refused(tmp_path):
    """DNS rebinding: the attacker's page sends its own name as Host and Origin."""
    client, app, _ = server(tmp_path, base_url="http://attacker.example:8090")
    origin = {"Origin": "http://attacker.example:8090"}
    assert client.get("/api/config").status_code == 403
    assert client.post("/api/command", json={"type": "stop_all"}, headers=origin).status_code == 403
    assert client.post("/api/faces/wipe", headers=origin).status_code == 403
    with pytest.raises(WebSocketDisconnect) as closed, live(client, headers=origin) as ws:
        ws.receive_json()
    assert closed.value.code == 1008 and app.ran == []


def test_this_pc_by_address_or_name_is_answered(tmp_path):
    name = socket.gethostname()
    for base in (PC, "http://127.0.0.1:8090", "http://localhost:8090", "http://[::1]:8090",
                 f"http://{name}:8090", f"http://{name}.local:8090", f"http://{name.upper()}:8090"):
        client, _, _ = server(tmp_path, base_url=base)
        assert client.get("/api/status").status_code == 200, base
        with live(client) as ws:
            assert ws.receive_json()["type"] == "status"


def test_this_pc_needs_no_pin_only_as_localhost(tmp_path):
    """A page on this PC opened under another name could be a renamed web site: it needs the PIN."""
    local, _, guard = server(tmp_path, base_url="http://127.0.0.1:8090", client=THIS_PC)
    guard.set_pin("1234")
    assert local.get("/api/status").status_code == 200
    assert TestClient(local.app, base_url="http://localhost:8090", client=THIS_PC).get("/api/status").status_code == 200
    by_name = TestClient(local.app, base_url=f"http://{socket.gethostname()}:8090", client=THIS_PC)
    assert by_name.get("/api/status").status_code == 401
    assert by_name.get("/api/access").json()["needs_pin"] is True
    with live(by_name) as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    assert closed.value.code == 4401


def test_cross_site_requests_are_refused_even_without_an_origin(tmp_path):
    """<img src=http://127.0.0.1:8090/api/discover> on another site: no Origin, but Sec-Fetch-Site."""
    client, app, _ = server(tmp_path, base_url="http://127.0.0.1:8090", client=THIS_PC)
    assert client.get("/api/discover", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/discover", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert app.discovered == 0
    assert client.get("/api/discover", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/api/discover", headers={"Sec-Fetch-Site": "none"}).status_code == 200  # typed in
    assert client.get("/api/discover").status_code == 200  # tools send none
    assert app.discovered == 3


# --- the live connection ----------------------------------------------------------------------------


def until_closed(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        for _ in range(50):  # status comes 10 times a second
            ws.receive_json()
    return closed.value.code


@pytest.mark.parametrize("revoke", ["forget_devices", "set_pin", "sign_out"])
def test_signing_a_device_out_closes_its_live_connection(tmp_path, revoke):
    client, app, guard = server(tmp_path)
    guard.set_pin("1234")
    cookie = client.post("/api/access/login", json={"pin": "1234"}).cookies[access.COOKIE]
    with live(client) as ws:
        assert ws.receive_json()["type"] == "status"
        if revoke == "forget_devices":
            guard.forget_devices()
        elif revoke == "set_pin":
            guard.set_pin("5678")
        else:
            guard.sign_out(cookie)
        ws.send_json({"type": "manual", "camera": "cam1", "pan": 5})
        assert until_closed(ws) == SIGNED_OUT  # the page connects again (and gets 4401 if it must sign in)
    assert app.ran == []
    with live(client) as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    assert closed.value.code == 4401


def test_switching_the_pin_on_closes_other_devices_live_connections(tmp_path):
    client, _, guard = server(tmp_path)
    with live(client) as ws:
        assert ws.receive_json()["type"] == "status"
        guard.set_pin("1234")
        assert until_closed(ws) == SIGNED_OUT


def test_a_signed_in_device_keeps_its_live_connection(tmp_path):
    client, app, guard = server(tmp_path)
    guard.set_pin("1234")
    client.post("/api/access/login", json={"pin": "1234"})
    other = TestClient(client.app, base_url=PC, client=("192.168.1.30", 50000))
    other.post("/api/access/login", json={"pin": "1234"})
    with live(client) as ws:
        guard.sign_out(other.cookies[access.COOKIE])  # another device signed out
        ws.send_json({"type": "tracking", "camera": "cam1", "on": True, "id": 3})
        replies = [m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "reply"]
        assert replies and replies[0]["request"] == 3 and app.ran


def test_a_failing_status_closes_the_live_connection_and_is_logged(tmp_path):
    client, app, _ = server(tmp_path)
    app.status_fails_after = 1
    with live(client) as ws:
        assert ws.receive_json()["type"] == "status"
        assert until_closed(ws) == 1011  # the page then connects again instead of showing frozen states
    assert any(level == "error" and "ZeroDivisionError" in message for _, message, level in app.log.events)


# --- PIN ----------------------------------------------------------------------------------------------


def test_setting_the_pin_hashes_away_from_the_event_loop(tmp_path, monkeypatch):
    client, _, guard = server(tmp_path, base_url="http://127.0.0.1:8090", client=THIS_PC)
    set_pin, on_loop = guard.set_pin, []

    def watched(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return set_pin(*args, **kwargs)

    monkeypatch.setattr(guard, "set_pin", watched)
    assert client.post("/api/access/pin", json={"pin": "2468"}).status_code == 200
    assert client.post("/api/access/pin", json={"pin": "12"}).status_code == 422
    assert on_loop == [False, False] and guard.enabled


# --- settings and show files ----------------------------------------------------------------------------


def test_settings_carry_a_revision_against_stale_pages(tmp_path):
    client, app, _ = server(tmp_path)
    cfg = client.get("/api/config").json()
    assert cfg["_rev"] == 0
    assert client.post("/api/config", json=cfg).status_code == 200  # the page's copy was current
    assert app.config_rev == 1 and app.applied[-1][1] is False
    r = client.post("/api/config", json=cfg)  # the same old copy again: another device changed them since
    assert r.status_code == 409 and "another device" in r.json()["detail"]
    assert len(app.applied) == 1
    del cfg["_rev"]
    assert client.post("/api/config", json=cfg).status_code == 200  # tools without a revision
    assert "_rev" not in client.get("/api/show").json()


def test_a_value_that_is_not_a_number_is_refused_not_a_server_error(tmp_path):
    """NaN and Infinity are refused in the settings (422); the answer must not fail to be written."""
    client, app, _ = server(tmp_path)
    for route, body in (("/api/config", '{"cameras": [{"id": "cam1", "units_per_degree": NaN}]}'),
                        ("/api/show", '{"cameras": [{"id": "cam1", "units_per_degree": Infinity}]}'),
                        ("/api/camera-test", '{"id": "cam1", "units_per_degree": NaN}')):
        r = client.post(route, content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 422 and r.json()["detail"][0]["loc"], route
    assert not app.applied


def test_loading_a_show_file_keeps_this_pcs_own_settings(tmp_path):
    client, app, _ = server(tmp_path)
    app.config.performance_mode, app.config.auto_preset = "light", "low"
    app.config.model_override, app.config.engines = "yolo26n-pose-320", ["cpu"]
    app.config.web_port = 8091
    show = AppConfig(cameras=[CameraConfig(id="cam1", name="Pulpit")], performance_mode="hyper", auto_preset="high")
    assert client.post("/api/show", json=json.loads(show.model_dump_json())).status_code == 200
    cfg, whole = app.applied[-1]
    assert whole and cfg.cameras[0].name == "Pulpit"
    assert (cfg.performance_mode, cfg.auto_preset, cfg.model_override, cfg.engines, cfg.web_port) == \
        ("light", "low", "yolo26n-pose-320", ["cpu"], 8091)


def test_a_show_file_does_not_map_mics_to_faces_this_pc_lacks(tmp_path):
    client, app, _ = server(tmp_path)
    here = app.faces.store.add("Pastor", [0.1] * 4, b"")
    show = AppConfig(cameras=[CameraConfig(id="cam1")])
    show.audio.mappings = [MicMapping(channel=1, face="elsewhere"), MicMapping(channel=2, face=here.id)]
    assert client.post("/api/show", json=json.loads(show.model_dump_json())).status_code == 200
    assert [m.face for m in app.applied[-1][0].audio.mappings] == ["", here.id]


# --- registered faces -----------------------------------------------------------------------------------


def test_face_delete_never_deletes_other_files(tmp_path):
    app = StubApp(tmp_path)
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"\xff\xd8")
    s = session()
    app.sessions = {"cam1": s}
    for made_up in ("../victim", str(tmp_path / "victim")):
        assert app.faces.command(s, "face_delete", {"id": made_up})["ok"]
    assert victim.exists()
    face = app.faces.store.add("Pastor", [0.1] * 4, b"\xff\xd8jpeg")
    app.faces.command(s, "face_delete", {"id": face.id})
    assert face.id not in app.faces.store.faces and not (tmp_path / "faces" / f"{face.id}.jpg").exists()


def test_deleting_a_face_clears_it_from_the_mic_mappings(tmp_path):
    app = StubApp(tmp_path)
    pastor = app.faces.store.add("Pastor", [0.1] * 4, b"")
    leader = app.faces.store.add("Leader", [0.2] * 4, b"")
    s, other = session(faces=[pastor.id, leader.id]), session("cam2", faces=[leader.id])
    app.sessions = {"cam1": s, "cam2": other}
    app.config.audio.mappings = [MicMapping(channel=1, face=pastor.id), MicMapping(channel=2, face=leader.id)]
    app.faces.command(s, "face_delete", {"id": pastor.id})
    app.faces.command(s, "face_delete", {"id": leader.id})  # cam2 still uses this one: it stays
    assert [m.face for m in app.config.audio.mappings] == ["", leader.id]
    saved = json.loads(app.config_path.read_text())
    assert [m["face"] for m in saved["audio"]["mappings"]] == ["", leader.id]
    app.faces.wipe()
    assert [m.face for m in app.config.audio.mappings] == ["", ""]
    assert [m["face"] for m in json.loads(app.config_path.read_text())["audio"]["mappings"]] == ["", ""]


def test_a_cancelled_or_replaced_face_capture_is_not_pending_for_ever(tmp_path):
    app = StubApp(tmp_path)
    s = session()
    app.sessions = {"cam1": s}
    first = app.faces.command(s, "face_capture", {"name": "Pastor"})["capture"]
    assert app.faces.camera_status("cam1")["face_capturing"]
    app.faces.command(s, "face_guide", {"on": False})  # Cancel: the worker forgets the capture
    assert not app.faces.camera_status("cam1")["face_capturing"]
    app.faces.command(s, "face_capture", {"name": "Pastor"})
    second = app.faces.command(s, "face_capture", {"name": "Pastor"})["capture"]  # replaces it in the worker
    assert list(app.faces.captures) == [second] and second != first
    app.faces.on_message({"type": "face_captured", "camera": "cam1", "capture": second,
                          "feature": [0.1] * 4, "jpeg": b""})
    assert not app.faces.camera_status("cam1")["face_capturing"]
    assert [f["name"] for f in app.faces.store.listing()] == ["Pastor"]


def test_a_new_vision_worker_is_given_the_registration_under_way(tmp_path):
    app = StubApp(tmp_path)
    s = session()
    app.sessions = {"cam1": s}
    number = app.faces.command(s, "face_capture", {"name": "Pastor"})["capture"]
    app.commands.sent.clear()
    app.faces.worker_started()  # the old worker and its pending capture are gone
    assert {"type": "face_capture", "camera": "cam1", "capture": number} in app.commands.sent
    app.sessions = {}  # the camera was removed with the settings that restarted the worker
    app.faces.worker_started()
    assert not app.faces.captures

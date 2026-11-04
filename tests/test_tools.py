"""The lab and simulator tools in tools/: the parts that can be checked without cameras."""

import argparse
import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import trackxen
from trackxen.devices import visca
from trackxen.sim.fake_camera import FakeCamera

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
SIM_CAMERA = {"id": "cam1", "host": "127.0.0.1", "ndi": "TRACKXEN SIM 1", "vmix_input": ""}
REAL_CAMERA = {"id": "cam1", "host": "192.168.1.50", "ndi": "BIRDDOG-P400 (CAM1)", "vmix_input": ""}


def load_tool(name: str, monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))  # the tools import their helpers as `_common`
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    tool = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, tool)  # (dataclasses look their module up there)
    spec.loader.exec_module(tool)
    return tool


class Clock:
    """Stands in for the time module, so waits in the tools take no real time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def strftime(self, fmt: str, *args) -> str:
        return "20261102"

    def time(self) -> float:
        return 1.8e9 + self.now

    def localtime(self, *args):
        import time

        return time.localtime(*args)


def test_visca_probe_info_only_reads(monkeypatch):
    """`info` sends no stop, which would halt a move someone else is making; `stop` still stops."""
    tool = load_tool("visca_probe", monkeypatch)
    stops = {visca.pan_tilt_stop(), visca.zoom_stop()}

    async def run(command: str) -> set[bytes]:
        cam = FakeCamera()
        port = await cam.start(port=0)
        try:
            args = tool.build_parser().parse_args(["127.0.0.1", "--port", str(port), "--timeout", "0.2", command])
            assert await tool.main(args) == 0
            return stops & set(cam.received)
        finally:
            cam.stop()

    assert asyncio.run(run("info")) == set()
    assert asyncio.run(run("stop")) == stops


def test_env_check_fails_when_the_onnxruntime_check_dies(monkeypatch, tmp_path):
    """A child process that crashes (missing ONNX Runtime, a DLL that doesn't load) is a failure."""
    monkeypatch.setenv("YOLO_AUTOINSTALL", "False")
    tool = load_tool("env_check", monkeypatch)
    monkeypatch.setattr(tool, "results_path", lambda name, suffix: tmp_path / f"{name}{suffix}")
    monkeypatch.setattr(tool, "check_pytorch", lambda: tool.show("OK", "python", "test"))
    monkeypatch.setattr(tool, "subprocess", SimpleNamespace(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3)))
    assert tool.main(argparse.Namespace(runtime_only=False, quick=True, report=None)) == 1
    saved = json.loads((tmp_path / "env-check.json").read_text(encoding="utf-8"))
    assert any(v["status"] == "FAIL" for v in saved.values())


def test_gpu_libs_manifest_needs_dlls_from_every_package(monkeypatch, tmp_path):
    tool = load_tool("gpu_libs_manifest", monkeypatch)
    assert tool.unlisted(json.loads(tool.OUT.read_text(encoding="utf-8"))["files"]) == []  # the shipped list
    out = tmp_path / "gpu-libs.json"
    monkeypatch.setattr(tool, "OUT", out)
    # run off Windows: the same NVIDIA packages hold .so files, so no DLL is found
    dist = SimpleNamespace(version="1.0", files=["nvidia/cu13/lib/libcudart.so.13"], locate_file=lambda r: tmp_path / r)
    monkeypatch.setattr(tool, "md", SimpleNamespace(distribution=lambda name: dist))
    monkeypatch.setattr(tool, "wheel_url", lambda client, name, version: ("https://example.invalid/w.whl", ""))
    monkeypatch.setattr(tool, "zip_members", lambda client, url: (1000, {}))
    monkeypatch.setattr(tool.sys, "argv", ["gpu_libs_manifest.py"])
    assert tool.main() == 1 and not out.exists()
    # the build's --check catches such a list too, whatever wrote it
    versions = {name: "1.0" for name in tool.PACKAGES}
    out.write_text(json.dumps({"id": "x", "versions": versions, "files": []}), encoding="utf-8")
    assert tool.check() == 1
    files = [{"path": f"{name}.dll", "package": name} for name in tool.PACKAGES]
    out.write_text(json.dumps({"id": "x", "versions": versions, "files": files}), encoding="utf-8")
    assert tool.check() == 0


class FakeApi:
    """Stands in for sim_scenarios.Api: records the commands, answers from fixed data."""

    def __init__(self, status: dict, config: dict, fail: str = ""):
        self.camera, self.status, self.config, self.fail = "cam1", status, config, fail
        self.commands: list[tuple[str, dict]] = []

    def get(self, path: str) -> dict:
        return self.config if path == "/api/config" else self.status

    def cmd(self, kind: str, **body) -> dict:
        self.commands.append((kind, body))
        if kind == self.fail:
            raise RuntimeError(f"{kind}: failed")
        return {"ok": True}

    def cam(self) -> dict:
        return self.status["cameras"][0]


@pytest.mark.parametrize("vmix, fail", [({"connected": False}, ""),  # no fake vMix running
                                         ({"connected": True, "suggested": {"cam1": "abc"}}, "slot")])  # an error
def test_on_air_scenario_puts_the_vmix_settings_back(monkeypatch, vmix, fail):
    tool = load_tool("sim_scenarios", monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    config = {"vmix": {"host": "10.0.0.9", "port": 8099}, "cameras": [{**SIM_CAMERA, "vmix_input": "7"}]}
    api = FakeApi({"vmix": vmix, "cameras": [{"id": "cam1"}]}, config, fail)
    if fail:
        with pytest.raises(RuntimeError):
            tool.on_air(api)
    else:
        assert tool.on_air(api)[0] is False
    assert ("connections", {"vmix": {"host": "10.0.0.9", "port": 8099}}) in api.commands[-2:]
    assert ("vmix_input", {"input": "7"}) in api.commands[-2:]


def test_auto_select_scenario_reports_a_target_never_seen(monkeypatch):
    tool = load_tool("sim_scenarios", monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    api = FakeApi({"cameras": [{"id": "cam1", "state": "tracking", "target": 3, "tracks": [], "zones": {}}]},
                  {"cameras": [SIM_CAMERA]})
    ok, detail = tool.auto_select(api)
    assert not ok and "never" in detail


class FakeServer:
    """GET and POST of the simulator tools, answered from fixed data; POSTs are recorded."""

    def __init__(self, camera: dict, status: dict, fail_after: int = 0):
        self.config = {"cameras": [camera]}
        self.status, self.fail_after, self.gets = status, fail_after, 0
        self.posts: list[dict] = []
        self.slots = {"active": 1, "slots": [{"auto_select": {}}, {"auto_select": {"enabled": False, "start_time": 3.0}}]}

    def get(self, url: str) -> dict:
        self.gets += 1
        if self.fail_after and self.gets > self.fail_after:
            raise OSError("timed out")
        for path, reply in (("/api/config", self.config), ("/api/slots/", self.slots), ("/events", [])):
            if path in url:
                return reply
        return self.status

    def post(self, url: str, body: dict) -> dict:
        self.posts.append(body)
        return {"ok": True}


def sim_status(target=None) -> dict:
    track = {"id": 1, "missed": 0, "box": [100, 100, 200, 400], "keypoints": [[150, 120, 0.9]] * 17}
    cam = {"id": "cam1", "state": "searching", "target": target, "tracks": [track], "command": [0, 0, 0],
           "pose": {"pan": 0.0}, "frame": {"width": 1920, "height": 1080}, "framing": {"anchor_x": 0.5, "anchor_y": 0.3}}
    return {"cameras": [cam], "stats": {}, "problems": [], "worker_alive": True}


def test_sim_test_reports_a_target_never_seen_and_stops_tracking(monkeypatch):
    tool = load_tool("sim_test", monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    server = FakeServer(SIM_CAMERA, sim_status(target=None))
    monkeypatch.setattr(tool, "get", server.get)
    monkeypatch.setattr(tool, "post", server.post)
    assert tool.main(argparse.Namespace(port=8090, seconds=2.0, yes=False)) == 1
    assert server.posts[-1] == {"type": "tracking", "camera": "cam1", "on": False}


def test_sim_test_stops_tracking_when_the_engine_stops_answering(monkeypatch):
    tool = load_tool("sim_test", monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    server = FakeServer(SIM_CAMERA, sim_status(target=1), fail_after=5)
    monkeypatch.setattr(tool, "get", server.get)
    monkeypatch.setattr(tool, "post", server.post)
    with pytest.raises(OSError):
        tool.main(argparse.Namespace(port=8090, seconds=30.0, yes=False))
    assert server.posts[-1] == {"type": "tracking", "camera": "cam1", "on": False}


@pytest.mark.parametrize("name", ["sim_test", "sim_load"])
def test_simulator_tools_leave_real_cameras_alone(monkeypatch, name):
    """The installed TrackXen answers on the same port: without --yes its cameras are never driven."""
    tool = load_tool(name, monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    server = FakeServer(REAL_CAMERA, sim_status(target=1))
    monkeypatch.setattr(tool, "get", server.get)
    monkeypatch.setattr(tool, "post", server.post)
    assert tool.main(argparse.Namespace(port=8090, seconds=1.0, minutes=0.0, yes=False)) != 0
    assert server.posts == []


def test_sim_scenarios_leave_real_cameras_alone(monkeypatch, capsys):
    tool = load_tool("sim_scenarios", monkeypatch)
    api = FakeApi({"cameras": [{"id": "cam1", "slot": 0}]}, {"cameras": [{**REAL_CAMERA, "lens": {}}]})
    monkeypatch.setattr(tool, "Api", lambda port: api)
    assert tool.main(argparse.Namespace(port=8090, names=[], yes=False)) != 0
    assert api.commands == [] and "--yes" in capsys.readouterr().out


def test_sim_load_puts_auto_select_back(monkeypatch):
    tool = load_tool("sim_load", monkeypatch)
    monkeypatch.setattr(tool, "time", Clock())
    server = FakeServer(SIM_CAMERA, sim_status(target=1))
    monkeypatch.setattr(tool, "get", server.get)
    monkeypatch.setattr(tool, "post", server.post)
    assert tool.main(argparse.Namespace(port=8090, minutes=0.05, yes=False)) == 0
    assert server.posts[-2:] == [
        {"type": "tracking", "camera": "cam1", "on": False},
        {"type": "slot_settings", "camera": "cam1", "slot": 1, "settings": {"auto_select": {"enabled": False, "start_time": 3.0}}}]


def test_motion_bench_gives_the_same_numbers_each_time_and_puts_the_clock_back(monkeypatch, tmp_path):
    """The smoothness bench runs the real tracking code in virtual time: one case and seed always gives the same
    result, the real clock and UDP sockets are back afterwards, and --check catches a walk followed in stop-starts."""
    import time

    from trackxen.devices import udp

    tool = load_tool("motion_bench", monkeypatch)
    real = time.monotonic, udp.open_udp
    case = tool.Case.parse("walk1.2-p400-50")
    results = tool.run_cases([case], seeds=[1])
    assert (time.monotonic, udp.open_udp) == real
    saved = tmp_path / "bench.json"
    assert tool.main([case.name, "--seeds", "1", "--jobs", "1", "--check", "--json", str(saved)]) == 0
    assert json.loads(saved.read_text(encoding="utf-8")) == results
    rows = tool.summarize(results)
    assert rows[case.name]["pan"]["starts_min"] == 0 and tool.check(rows) == []
    rows[case.name]["pan"]["starts_min"] = 29.9  # as at 0d953f9
    assert tool.check(rows) == ["walk1.2-p400-50: pan starts_min 29.90 > 10"]
    with pytest.raises(ValueError):
        tool.Case.parse("walk1.2-p400")


def test_version_is_the_same_everywhere():
    """The version is written in several files that are bumped by hand together."""
    v = trackxen.__version__
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    package = json.loads((ROOT / "ui" / "package.json").read_text(encoding="utf-8"))["version"]
    iss = re.search(r'#define AppVersion "([^"]+)"', (ROOT / "packaging" / "trackxen.iss").read_text(encoding="utf-8"))
    resource = (ROOT / "packaging" / "version.txt").read_text(encoding="utf-8")
    numbers = ", ".join((v + ".0").split("."))
    assert pyproject == package == iss.group(1) == v
    assert resource.count(f"({numbers})") == 2 and resource.count(f"'{v}'") == 2
    from trackxen.core.gpu_libs import USER_AGENT

    assert USER_AGENT.startswith(f"TrackXen/{v} ")
    assert (ROOT / "LICENSE-NOTICE.txt").read_text(encoding="utf-8").startswith(f"TrackXen {v} ")
    assert f"<span>{v} " in (ROOT / "ui" / "src" / "settings" / "SystemSettings.svelte").read_text(encoding="utf-8")
    assert f"TrackXen-Setup-{v}.exe" in (ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")

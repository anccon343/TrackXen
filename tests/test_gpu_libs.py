import hashlib
import importlib.util
import io
import json
import os
import struct
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from trackxen.core import gpu_libs
from trackxen.core.gpu_libs import GpuLibraries, wanted

ROOT = Path(__file__).resolve().parent.parent
MEMBERS = {  # path in the wheel -> (size, compressed?)
    "nvidia/cu13/bin/x86_64/cudart64_13.dll": (300_000, True),
    "nvidia/cudnn/bin/cudnn64_9.dll": (120_000, True),
    "tensorrt_libs/nvinfer_10.dll": (200_000, False),
    "tensorrt_libs/nvinfer_builder_resource_sm89_10.dll": (150_000, True),
    "tensorrt_libs/nvinfer_builder_resource_sm120_10.dll": (150_000, True),
    "tensorrt_libs/nvinfer_builder_resource_ptx_10.dll": (100_000, True),
}


class Wheels(ThreadingHTTPServer):
    """A tiny file server that answers HTTP range requests, like PyPI's; it can be made to fail."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.fail_next, self.ranges = files, 0, True
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        body = self.server.files[self.path]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        body = self.server.files[self.path]
        if self.server.fail_next:
            self.server.fail_next -= 1
            self.send_response(500)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng and self.server.ranges:
            start, end = (int(v) for v in rng.removeprefix("bytes=").split("-"))
            body = body[start:end + 1]
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_wheel() -> tuple[bytes, dict[str, bytes]]:
    contents = {path: os.urandom(size // 2) * 2 for path, (size, _) in MEMBERS.items()}  # half random: compresses a bit
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, data in contents.items():
            z.writestr(path, data, zipfile.ZIP_DEFLATED if MEMBERS[path][1] else zipfile.ZIP_STORED)
    return buf.getvalue(), contents


def manifest_for(wheel: bytes, contents: dict[str, bytes], url: str) -> dict:
    """What tools/gpu_libs_manifest.py writes, made straight from the wheel's bytes."""
    files = []
    for info in zipfile.ZipFile(io.BytesIO(wheel)).infolist():
        name_len, extra_len = struct.unpack("<HH", wheel[info.header_offset + 26:info.header_offset + 30])
        builder = info.filename.split("resource_")[-1].split("_")[0] if "builder_resource" in info.filename else None
        files.append({"path": info.filename, "size": info.file_size,
                      "sha256": hashlib.sha256(contents[info.filename]).hexdigest(), "url": url,
                      "data_offset": info.header_offset + 30 + name_len + extra_len, "compressed": info.compress_size,
                      "method": info.compress_type, "crc": info.CRC, "gpu": builder, "package": "test"})
    return {"id": "cuda-test", "versions": {}, "files": files}


@pytest.fixture
def wheels(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_libs, "RETRY_AFTER", (0.01, 0.01))
    wheel, contents = make_wheel()
    server = Wheels({"/test.whl": wheel})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manifest = tmp_path / "gpu-libs.json"
    manifest.write_text(json.dumps(manifest_for(wheel, contents, server.base + "/test.whl")))
    yield server, manifest, contents
    server.shutdown()


def test_downloads_only_what_this_gpu_needs(tmp_path, wheels):
    server, manifest, contents = wheels
    libs = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    assert {Path(f["path"]).name for f in libs.files} == {
        "cudart64_13.dll", "cudnn64_9.dll", "nvinfer_10.dll", "nvinfer_builder_resource_sm89_10.dll"}
    assert not libs.check() and libs.state == "checking"
    libs.download()
    assert libs.ready and libs.done == libs.total == sum(f["compressed"] for f in libs.files)
    for f in libs.files:
        assert (libs.dir / f["path"]).read_bytes() == contents[f["path"]]
    assert not list(libs.dir.rglob("*.part"))
    server.shutdown()  # nothing is fetched again: the files are checked on disk
    again = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    assert again.check() and again.missing() == []


def test_a_gpu_tensorrt_has_no_file_for_gets_the_generic_one(wheels):
    _, manifest, _ = wheels
    files = json.loads(manifest.read_text())["files"]
    names = {Path(f["path"]).name for f in wanted(files, {"sm130"})}
    assert "nvinfer_builder_resource_ptx_10.dll" in names and "nvinfer_builder_resource_sm89_10.dll" not in names
    assert wanted(files, set()) == []  # no NVIDIA GPU: nothing


def test_a_failed_request_is_tried_again(tmp_path, wheels):
    server, manifest, _ = wheels
    server.fail_next = 1
    libs = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    libs.download()
    assert libs.ready and libs.done == libs.total  # the failed try isn't counted twice


def test_a_damaged_file_is_refused_and_the_good_ones_kept(tmp_path, wheels):
    _, manifest, _ = wheels
    data = json.loads(manifest.read_text())
    bad = next(f for f in data["files"] if f["path"].endswith("cudnn64_9.dll"))
    bad["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data))
    libs = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    with pytest.raises(OSError, match="didn't match"):
        libs.download()
    assert not (libs.dir / bad["path"]).exists() and not list(libs.dir.rglob("*.part"))
    assert (libs.dir / "nvidia/cu13/bin/x86_64/cudart64_13.dll").exists()  # downloaded before it: kept
    assert [Path(f["path"]).name for f in libs.missing()] == ["cudnn64_9.dll", "nvinfer_10.dll",
                                                               "nvinfer_builder_resource_sm89_10.dll"]


def test_a_server_without_range_requests_is_reported(tmp_path, wheels):
    server, manifest, _ = wheels
    server.ranges = False
    libs = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    with pytest.raises(OSError, match="instead of a part"):
        libs.download()


def test_run_reports_failure_and_tries_again_when_asked(tmp_path, wheels, monkeypatch):
    server, manifest, _ = wheels
    server.fail_next = 100
    libs = GpuLibraries(tmp_path / "libs", manifest, generations={"sm89"})
    ready = threading.Event()
    libs.start(on_ready=ready.set)
    for _ in range(200):
        if libs.state == "failed":
            break
        threading.Event().wait(0.01)
    assert libs.state == "failed" and libs.problems()[0]["level"] == "error"
    server.fail_next = 0
    libs.retry()  # "Download again"
    assert ready.wait(5) and libs.ready and libs.problems() == []


def test_manifest_tool_reads_a_wheels_directory_over_range_requests(wheels):
    server, _, _ = wheels
    spec = importlib.util.spec_from_file_location("gpu_libs_manifest", ROOT / "tools" / "gpu_libs_manifest.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with httpx.Client() as client:
        size, members = tool.zip_members(client, server.base + "/test.whl")
    local = {i.filename: i for i in zipfile.ZipFile(io.BytesIO(server.files["/test.whl"])).infolist()}
    assert size == len(server.files["/test.whl"]) and members.keys() == local.keys()
    for name, info in members.items():
        assert (info.header_offset, info.compress_size, info.CRC) == (
            local[name].header_offset, local[name].compress_size, local[name].CRC)

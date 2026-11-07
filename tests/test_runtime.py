import json
import os
from pathlib import Path, PurePosixPath

import pytest

from trackxen.core import gpu_libs
from trackxen.vision import runtime


class FakeOrt:
    """ONNX Runtime's GPU build: lists every engine, but those in `broken` can't load and the
    session silently runs on the CPU instead (what it does without the NVIDIA libraries)."""

    def __init__(self, broken: set[str]):
        self.broken = {runtime.PROVIDER_NAMES[e] for e in broken}
        self.opened: list[list] = []
        self.preloaded: list[dict] = []

    def get_available_providers(self):
        return list(runtime.PROVIDER_NAMES.values())

    def InferenceSession(self, path, providers):  # noqa: N802 - ONNX Runtime's name
        names = [p[0] if isinstance(p, tuple) else p for p in providers]
        self.opened.append(names)
        works = [n for n in names if n not in self.broken]

        class Session:
            def get_providers(self):
                return works

        return Session()

    def preload_dlls(self, **kwargs):
        self.preloaded.append(kwargs)


@pytest.fixture
def fake_ort(monkeypatch):
    def make(broken=()):
        ort = FakeOrt(set(broken))
        monkeypatch.setattr(runtime, "ort", ort)
        monkeypatch.setattr(runtime, "_prepared", {str(None)})
        monkeypatch.setattr(runtime, "_gpu_libs", None)
        return ort
    return make


def test_an_engine_that_falls_back_to_the_cpu_is_passed_over(fake_ort):
    ort = fake_ort(broken={"tensorrt"})
    _, engine = runtime.create_session("m.onnx")
    assert engine == "cuda" and [names[0] for names in ort.opened] == ["TensorrtExecutionProvider", "CUDAExecutionProvider"]
    fake_ort(broken={"tensorrt", "cuda", "directml"})
    assert runtime.create_session("m.onnx", engines=("tensorrt", "cuda", "cpu"))[1] == "cpu"
    with pytest.raises(RuntimeError, match="no inference engine"):
        runtime.create_session("m.onnx", engines=("tensorrt", "cuda"))


def test_the_downloaded_library_folders_are_the_ones_loaded(fake_ort, tmp_path, monkeypatch):
    """Every folder of the downloaded NVIDIA libraries (assets/gpu-libs.json) is put on the DLL path."""
    ort = fake_ort()
    files = json.loads(gpu_libs.MANIFEST.read_text(encoding="utf-8"))["files"]
    folders = {str(PurePosixPath(f["path"]).parent) for f in files}
    for folder in folders:
        (tmp_path / folder).mkdir(parents=True)
    added = []
    monkeypatch.setattr(os, "add_dll_directory", added.append, raising=False)  # (Windows only)
    monkeypatch.setenv("PATH", "")
    runtime.use_gpu_libraries(tmp_path)
    runtime.prepare_gpu_libraries()
    assert {Path(a).relative_to(tmp_path).as_posix() for a in added} == folders
    assert {Path(p["directory"]).relative_to(tmp_path).as_posix() for p in ort.preloaded} == {
        "nvidia/cu13/bin/x86_64", "nvidia/cudnn/bin"}

"""ONNX Runtime sessions that use the best engine this PC has.

Order of preference: TensorRT (NVIDIA, FP16, engines cached per GPU), CUDA, DirectML
(AMD/Intel GPUs, needs the onnxruntime-directml build), CPU. The CUDA/cuDNN/TensorRT DLLs come
from the Python packages in development, and from the folder core/gpu_libs.py downloads them to
in the installed app; PyTorch isn't needed at run time.
"""

from __future__ import annotations

import os
from pathlib import Path

import onnxruntime as ort

ENGINE_ORDER = ("tensorrt", "cuda", "directml", "cpu")
PROVIDER_NAMES = {
    "tensorrt": "TensorrtExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "directml": "DmlExecutionProvider",
    "cpu": "CPUExecutionProvider",
}
_gpu_libs: Path | None = None
_prepared: set[str] = set()


def use_gpu_libraries(folder: str | Path | None) -> None:
    """Load the NVIDIA DLLs from this folder (core/gpu_libs.py) instead of the Python packages."""
    global _gpu_libs
    _gpu_libs = Path(folder) if folder else None


def prepare_gpu_libraries() -> None:
    """Load the CUDA/cuDNN/TensorRT DLLs that ONNX Runtime's GPU providers need (once per folder)."""
    if str(_gpu_libs) in _prepared:
        return
    _prepared.add(str(_gpu_libs))
    if _gpu_libs:
        cuda, cudnn, tensorrt = (_gpu_libs / "nvidia" / "cu13" / "bin" / "x86_64", _gpu_libs / "nvidia" / "cudnn" / "bin",
                                 _gpu_libs / "tensorrt_libs")
        for folder in (cuda, cudnn, tensorrt):
            if folder.is_dir():
                os.add_dll_directory(str(folder))
                os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls(cuda=True, cudnn=False, msvc=True, directory=str(cuda))
                ort.preload_dlls(cuda=False, cudnn=True, msvc=False, directory=str(cudnn))
            except Exception:  # noqa: BLE001 - a missing library just means no GPU provider
                pass
        return
    try:
        import tensorrt_libs  # type: ignore

        libdir = os.path.dirname(tensorrt_libs.__file__)
        os.add_dll_directory(libdir)
        os.environ["PATH"] = libdir + os.pathsep + os.environ.get("PATH", "")
    except ImportError:
        pass
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
        except Exception:  # noqa: BLE001 - a missing optional library just means no GPU provider
            pass


def available_engines() -> list[str]:
    names = set(ort.get_available_providers())
    return [e for e in ENGINE_ORDER if PROVIDER_NAMES[e] in names]


def _provider_entry(engine: str, cache_dir: Path | None):
    if engine == "tensorrt":
        options = {"trt_fp16_enable": True, "trt_timing_cache_enable": True}
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            options.update(trt_engine_cache_enable=True, trt_engine_cache_path=str(cache_dir),
                           trt_timing_cache_path=str(cache_dir))
        return (PROVIDER_NAMES[engine], options)
    return PROVIDER_NAMES[engine]


def create_session(model_path: str | Path, engines: tuple[str, ...] = ENGINE_ORDER,
                   cache_dir: Path | None = None) -> tuple[ort.InferenceSession, str]:
    """Open a model with the first engine that actually works; returns (session, engine)."""
    prepare_gpu_libraries()
    available = set(available_engines())
    last_error: Exception | None = None
    for engine in engines:
        if engine not in available:
            continue
        try:
            providers = [_provider_entry(engine, cache_dir)]
            if engine != "cpu":
                providers.append(PROVIDER_NAMES["cpu"])
            session = ort.InferenceSession(str(model_path), providers=providers)
        except Exception as e:  # noqa: BLE001 - try the next engine
            last_error = e
            continue
        # ONNX Runtime silently falls back to CPU when a GPU provider can't load
        if session.get_providers()[0] == PROVIDER_NAMES[engine]:
            return session, engine
    if last_error:
        raise RuntimeError(f"no inference engine could load {model_path}") from last_error
    raise RuntimeError(f"no inference engine could load {model_path}")

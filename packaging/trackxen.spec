# PyInstaller build of TrackXen.exe (one folder):  .venv\Scripts\python -m PyInstaller packaging\trackxen.spec --noconfirm
#
# Ships: the engine, the web UI (build it first: cd ui && npm run build), the models of the four
# performance presets plus the face models, ONNX Runtime and the NDI runtime that comes with
# cyndilib. NVIDIA's CUDA, cuDNN and TensorRT DLLs are NOT shipped (4 GB): the app downloads the
# ones a PC needs on its first start (core/gpu_libs.py, from the list in core/assets/gpu-libs.json
# that tools/gpu_libs_manifest.py writes). They are kept out here even where PyInstaller would
# pull them in as dependencies of ONNX Runtime's GPU providers.
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - SPECPATH is set by PyInstaller
MODELS = [f"yolo26{m}-pose-{size}-b{batch}.onnx" for m, size in (("n", 640), ("s", 640), ("m", 640), ("m", 960))
          for batch in (1, 4)] + ["face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"]

missing = [m for m in MODELS if not os.path.exists(os.path.join(ROOT, "models", m))]
if missing:
    raise SystemExit(f"models missing: {missing} (tools/export_models.py, tools/get_face_models.py)")
if not os.path.exists(os.path.join(ROOT, "engine/trackxen/core/static/app/index.html")):
    raise SystemExit("web UI not built: cd ui && npm run build")
if not os.path.exists(os.path.join(ROOT, "engine/trackxen/core/assets/gpu-libs.json")):
    raise SystemExit("NVIDIA download list missing: python tools/gpu_libs_manifest.py")


def nvidia_dll(path: str) -> bool:
    """A DLL from NVIDIA's packages (downloaded on first start instead), or a copy of one that came with
    PyTorch (development only; PyInstaller finds its cuDNN when it looks for the ones left out)."""
    parts = path.replace("/", "\\").lower().split("\\")
    return "site-packages" in parts and any(p in parts for p in ("nvidia", "tensorrt_libs", "torch", "torchvision"))


datas = [(os.path.join(ROOT, "engine/trackxen/core/static"), "trackxen/core/static"),
         (os.path.join(ROOT, "engine/trackxen/core/assets"), "trackxen/core/assets")]
datas += [(os.path.join(ROOT, "models", m), "models") for m in MODELS]
binaries = []
hiddenimports = (collect_submodules("uvicorn") + ["win32pdh", "pynvml"]
                 + collect_submodules("trackxen", filter=lambda name: not name.startswith("trackxen.sim")))
for package in ("cyndilib", "onnxruntime"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[os.path.join(ROOT, "engine")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "torchvision", "ultralytics", "matplotlib", "pandas", "scipy", "tensorboard", "IPython",
              "tkinter", "polars", "sympy", "onnx", "onnxslim", "trackxen.sim", "tensorrt_libs", "nvidia"],
    noarchive=False,
)
a.binaries = [b for b in a.binaries if not nvidia_dll(b[1])]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="TrackXen", console=False,
    icon=os.path.join(ROOT, "packaging", "trackxen.ico"), version=os.path.join(ROOT, "packaging", "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="TrackXen")  # noqa: F821

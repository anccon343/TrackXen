# PyInstaller build of TrackXen.exe (one folder):  .venv\Scripts\pyinstaller packaging\trackxen.spec --noconfirm
#
# Ships: the engine, the web UI (build it first: cd ui && npm run build), the models of the four
# performance presets plus the face models, ONNX Runtime with its CUDA/cuDNN DLLs and TensorRT,
# and the NDI runtime that comes with cyndilib. The NVIDIA DLLs keep their site-packages layout
# (nvidia/cu13/bin, nvidia/cudnn/bin, tensorrt_libs) because ONNX Runtime looks for them there.
import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - SPECPATH is set by PyInstaller
SITE = sysconfig.get_paths()["purelib"]
MODELS = [f"yolo26{m}-pose-{size}-b{batch}.onnx" for m, size in (("n", 640), ("s", 640), ("m", 640), ("m", 960))
          for batch in (1, 4)] + ["face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"]

missing = [m for m in MODELS if not os.path.exists(os.path.join(ROOT, "models", m))]
if missing:
    raise SystemExit(f"models missing: {missing} (tools/export_models.py, tools/get_face_models.py)")
if not os.path.exists(os.path.join(ROOT, "engine/trackxen/core/static/app/index.html")):
    raise SystemExit("web UI not built: cd ui && npm run build")


def site_dlls(package: str) -> list[tuple[str, str]]:
    """Every DLL of a package, at the same place relative to site-packages."""
    out = []
    for path in glob.glob(os.path.join(SITE, package, "**", "*.dll"), recursive=True):
        out.append((path, os.path.relpath(os.path.dirname(path), SITE)))
    return out


datas = [(os.path.join(ROOT, "engine/trackxen/core/static"), "trackxen/core/static"),
         (os.path.join(ROOT, "engine/trackxen/core/assets"), "trackxen/core/assets")]
datas += [(os.path.join(ROOT, "models", m), "models") for m in MODELS]
binaries = site_dlls("nvidia") + site_dlls("tensorrt_libs")
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
              "tkinter", "polars", "sympy", "trackxen.sim"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="TrackXen", console=False,
    icon=os.path.join(ROOT, "packaging", "trackxen.ico"), version=os.path.join(ROOT, "packaging", "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="TrackXen")  # noqa: F821

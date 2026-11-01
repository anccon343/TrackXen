# TrackXen

Automatic speaker tracking for up to four BirdDog P400 PTZ cameras: Canon Auto Tracking
(RA-AT001)-style features, vMix tally-aware motion and microphone-driven targeting from a Midas
MR18 / Behringer X Air mixer. Personal, non-commercial project (AGPL-3.0-or-later).

- `docs/user-guide.md`: how to use it
- `docs/progress.md`: what is built, measured results, how to run everything
- `docs/questions.md`: questions and their answers
- `docs/plan.md`, `docs/reference.md`, `docs/research.md`: the plan, the Canon feature set, technology choices

## Development (Windows, Python 3.12, NVIDIA GPU)

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt -c requirements-lock.txt
cd ui && npm install && npm run build && cd ..
.venv\Scripts\python -m pytest
```

The models aren't in git. Export them with PyTorch and Ultralytics (see `requirements-dev.txt`),
`tools\export_models.py`, then `tools\fuse_preprocess.py`; the face models come from
`tools\get_face_models.py`.

Run with real cameras: `.venv\Scripts\python -m trackxen`, then open http://127.0.0.1:8090.
Without cameras: `tools\sim_camera.py` (a virtual P400) and `python -m trackxen --sim`.

## The Windows program

`powershell -ExecutionPolicy Bypass -File packaging\build.ps1` builds the web UI,
`dist\TrackXen\TrackXen.exe` (PyInstaller) and, with Inno Setup 6 installed
(`winget install JRSoftware.InnoSetup`), the setup program in `installer-output\`.
NVIDIA's CUDA, cuDNN and TensorRT libraries aren't in it: the program downloads the ones a PC
needs on its first start (`tools\gpu_libs_manifest.py` writes the list).

Lab tools for measuring the cameras live in `tools/` (see `tools/README.md`).

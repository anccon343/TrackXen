# TrackXen

Automatic speaker tracking for up to four BirdDog P400 PTZ cameras: Canon Auto Tracking
(RA-AT001)-style features, vMix tally-aware motion and microphone-driven targeting from a Midas
MR18 / Behringer X Air mixer. Personal, non-commercial project (AGPL-3.0-or-later).

- `docs/user-guide.md`: how to use it
- `docs/progress.md`: what is built, measured results, how to run everything
- `docs/questions.md`: open questions
- `docs/plan.md`, `docs/reference.md`, `docs/research.md`: the plan, the Canon feature set, technology choices

## Development (Windows, Python 3.12, NVIDIA GPU)

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python tools\export_models.py        (YOLO26 pose models)
.venv\Scripts\python tools\get_face_models.py      (face models)
cd ui && npm install && npm run build && cd ..
.venv\Scripts\python -m pytest
```

Run with real cameras: `.venv\Scripts\python -m trackxen`, then open http://127.0.0.1:8090.
Without cameras: `tools\sim_camera.py` (a virtual P400) and `python -m trackxen --sim`.

## The Windows program

`powershell -ExecutionPolicy Bypass -File packaging\build.ps1` builds the web UI, `dist\TrackXen\TrackXen.exe`
(PyInstaller) and, when Inno Setup 6 is installed, the setup program in `installer-output\`.

Lab tools for measuring the cameras live in `tools/` (see `tools/README.md`).

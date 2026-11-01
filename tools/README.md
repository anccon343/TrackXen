# Camera lab tools (Phase 1)

Small command-line tools to measure the real cameras, mixer and vMix before the tracker is built
on top of them. Results land in `lab-results/` (not in git).

Run them from the project folder in a normal PowerShell or Windows Terminal:

```
cd $HOME\Desktop\TrackXen
.venv\Scripts\python tools\discover.py
```

**Safety:** tools only *read* unless you add `--yes`. Anything that moves a camera ends with a
stop command, also when you press Ctrl+C. Don't run movement tests while a camera is live.

## Before the lab session

1. Update all P400s to the latest firmware.
2. In each P400's BirdUI → CamControl → FreeD: turn FreeD on and send it to this PC's IP. Use port
   40001 for camera 1, 40002 for camera 2, and so on.
3. Make sure this PC is on the same network as the cameras, the MR18 and vMix.

## The tools

| Tool | What it does | Moves the camera? |
|---|---|---|
| `discover.py` | Finds NDI sources, BirdDog cameras (REST API), MR18/X Air mixers and vMix | no |
| `ndi_probe.py` | Lists NDI sources, or receives one: frame rate, resolution, timing, snapshot | no |
| `visca_probe.py HOST info` | Reads power, pan/tilt, zoom and focus over VISCA | no |
| `visca_probe.py HOST --yes move ...` | Drives pan/tilt for a moment | **yes** |
| `visca_probe.py HOST --yes sweep ...` | Measures every speed step (with FreeD if given) | **yes** |
| `freed_probe.py --port 40001` | Shows FreeD rate and values | no |
| `xair_meters.py [HOST]` | Live input levels and channel names from the MR18 | no |
| `vmix_tally.py HOST` | vMix inputs and live Program/Preview changes | no |
| `recorder.py "SOURCE"` | Records NDI video + FreeD for replay tests | no |
| `env_check.py` | Checks this PC: GPU, models, ONNX Runtime engines, NDI | no |
| `ndi_test_source.py` | Sends a moving test picture as an NDI source | no |
| `fake_devices.py` | Pretend camera, mixer and vMix on this PC, for trying the tools | no |

## Lab session checklist

Do this for one camera first; the others only need steps 1–3.

```
.venv\Scripts\python tools\discover.py --vmix <vMix PC IP>
.venv\Scripts\python tools\ndi_probe.py "<NDI name>" --seconds 10 --snapshot
.venv\Scripts\python tools\ndi_probe.py "<NDI name>" --seconds 10 --low-bandwidth
.venv\Scripts\python tools\visca_probe.py <camera IP> info
.venv\Scripts\python tools\freed_probe.py --port 40001 --seconds 10 --csv
.venv\Scripts\python tools\visca_probe.py <camera IP> --yes sweep --axis pan --freed-port 40001
.venv\Scripts\python tools\visca_probe.py <camera IP> --yes sweep --axis tilt --speeds 1-20 --freed-port 40001
.venv\Scripts\python tools\xair_meters.py --seconds 30 --csv
.venv\Scripts\python tools\vmix_tally.py <vMix PC IP> --seconds 60
.venv\Scripts\python tools\recorder.py "<NDI name>" --freed-port 40001 --seconds 120
```

During the FreeD probe, move the camera with its remote or a controller, so the numbers change.
During the recording, have someone walk across the stage, sit down, stand up and talk at the podium.

Then send the `lab-results` folder back (or just tell Claude it's there if this PC ran the tools).

## Trying the tools without hardware

```
.venv\Scripts\python tools\fake_devices.py --freed-to 127.0.0.1:40001
```

Then, in a second window:

```
.venv\Scripts\python tools\visca_probe.py 127.0.0.1 info
.venv\Scripts\python tools\xair_meters.py 127.0.0.1
.venv\Scripts\python tools\vmix_tally.py 127.0.0.1
```

## Development and simulation tools

| Tool | What it does |
|---|---|
| `sim_camera.py` | A virtual P400 (VISCA, FreeD, NDI video of a stage with a walking speaker) |
| `sim_test.py` | Locks onto the speaker in TrackXen and measures framing, visibility and timing |
| `sim_scenarios.py [names]` | Canon's tracking rules end to end: auto select, fixed / halting areas, shooting range, manual operation, preset link, multi-person, registered faces |
| `export_models.py` | Exports the YOLO26 pose models to ONNX (needs PyTorch; run in its own process) |
| `fuse_preprocess.py` | Moves the image preparation into the exported models, so it runs on the GPU (run after exporting) |
| `gpu_libs_manifest.py` | Lists the NVIDIA DLLs the installed app downloads on its first start (`--check` in the build) |
| `sim_load.py` | Load and soak test: all cameras tracking at once, with measured results |
| `get_face_models.py` | Downloads the face models (YuNet, SFace) for Registered Face Tracking |
| `hardware_test.py` | Measures this PC and recommends Light / Auto / HYPER |
| `stats_view.py` | Shows a statistics log afterwards |

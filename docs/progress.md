# Progress log

The app is called **TrackXen** (code package `trackxen`). Open questions are in `docs/questions.md`.

## How to run it (development)

```
.venv\Scripts\python tools\sim_camera.py        (window 1: a virtual P400 with NDI video)
.venv\Scripts\python -m trackxen --sim          (window 2: TrackXen)
open http://127.0.0.1:8090                       (the UI; /test is the simple Phase 2 page)
.venv\Scripts\python tools\sim_test.py          (optional: automatic 30 s tracking test)
.venv\Scripts\python tools\sim_scenarios.py     (optional: Canon rules end to end, 10 scenarios)
.venv\Scripts\python tools\sim_load.py          (optional, with --sim-cameras 4: all cameras at once, measured)
.venv\Scripts\python tools\get_face_models.py   (once per PC: the face models for Registered Face Tracking)
.venv\Scripts\python tools\hardware_test.py     (measure the PC, recommend Light / Auto / HYPER)
.venv\Scripts\python tools\motion_bench.py --check   (pan/tilt smoothness in virtual time, about 10 s)
.venv\Scripts\python -m pytest                  (481 tests)
.venv\Scripts\python -m ruff check engine tools tests   (lint)
cd ui && npm run build                           (rebuild the UI after changes)
```

## 2026-10-31: Phase 0 and Phase 1 tools

**Environment**
- **Hardware:** Windows 11, Ryzen 9 7950X, RTX 4080 SUPER 16 GB (driver 595.59).
- **Python 3.12.10** (winget, this user) in `.venv`.
- **Development only:** PyTorch 2.11 (CUDA 12.8) and Ultralytics 8.4.195, for model export.
- **What the app runs on:**
  - ONNX Runtime GPU 1.31.1, with CUDA 13 + cuDNN 9 (`onnxruntime-gpu[cuda,cudnn]`) and TensorRT 10.16
    (`tensorrt-cu13-libs`)
  - cyndilib (NDI 6), OpenCV 5
  - FastAPI / Uvicorn
  - the UI: Svelte 5, Vite 8, TypeScript 5
- **Exact versions:** `requirements-lock.txt`, `ui/package-lock.json`.

**Lab tools** (`tools/`, see `tools/README.md`): discover, ndi_probe, visca_probe (with speed sweep),
freed_probe, xair_meters, vmix_tally, recorder, env_check, ndi_test_source, fake_devices. All were
checked against the fakes and a local NDI test source. The real measurements wait for the cameras.

## 2026-10-31: Phase 2, one camera follows one person (done in simulation)

**Built**
- **Virtual P400** (`sim/virtual_camera.py`, `tools/sim_camera.py`):
  - VISCA + FreeD
  - live NDI video of a stage with a walking person (photo cut-outs, so the real detector works)
  - video delay 80 ms, command delay 30 ms
- **Vision worker** (separate process):
  - NDI in; YOLO26-pose through ONNX Runtime, starting on CUDA and switching to TensorRT when built
  - ByteTrack-style tracker
  - preview JPEGs
  - measured statistics: arrival gaps, NDI's own dropped frames, skipped frames, time of each stage
- **Control:**
  - lens model (zoom ↔ field of view)
  - Kalman prediction in room angles
  - controller with framing point, dead zone (Canon's halting area), feed-forward, speed and
    acceleration limits, mapping onto real speed steps, auto zoom
  - camera link: coalesced VISCA, FreeD history, auto-reconnect
- **Core:**
  - sessions: target lock, re-finding a lost target, lost → hold or go home
  - watchdog: stops cameras when detections stop; restarts a dead worker
  - statistics and a text log
  - web API + WebSocket
- **Statistics** (researched so every number is real; see research.md 3.5):
  - **GPU:** NVML for GPU busy time (it sees CUDA/TensorRT work); Windows GPU engine counters for
    3D/video (vMix's share) and our video memory.
  - **CPU and network:** psutil.
  - **Timings:** percentiles, not averages.
  - **Log files:** `data/logs/TrackXen-YYYYMMDD.log` (readable), plus `.jsonl` copies.

**Measured in simulation (60 s, person walking 4°/s with stops at the podium)**
- **Visibility:** target visible in 98% of samples; never lost; one automatic re-find after an ID
  change.
- **Framing:** horizontal error median 1.7% of the frame width, 95th percentile 5.5%.
- **Timing:** 30 detections/s; frame → result 16 ms (median); VISCA round trip 0.13 ms (local).

**Bugs found and fixed along the way (by testing, not guessing)**
- **Windows UDP:** after sending to a closed port, Python 3.12's Windows event loop stops reading a UDP
  socket for good. That would have silently killed VISCA/FreeD/mixer links after a camera reboot.
  - Reproduced, then fixed with `SIO_UDP_CONNRESET` through `WSAIoctl`. My first fix silently did
    nothing (Python doesn't define the constant); caught by re-testing.
  - `devices/udp.py`, `tests/test_udp.py`.
- **Thresholds:** the detector discarded detections below 0.4 before the tracker saw them, and the
  tracker thresholds were stricter than ByteTrack practice. People at the podium (scoring 0.35-0.6)
  were lost. Fixed: thresholds as in ByteTrack/Ultralytics (also exposed in Advanced mode).
- **Re-finding:** only searched near an extrapolated position, for 3 s. Now: last known position, a
  search circle that grows with time, 10 s, and ambiguous candidates are refused.
- **Manual control:** releasing a button with tracking off left the camera moving. Now it always stops.
- **Statistics:**
  - Source frame rate came from the median gap (showed 32.3 instead of 30); now frames / time.
  - The per-process GPU share summed several NVIDIA samples (could exceed 100%); now averaged.
  - The problem check used a counter name that didn't exist (false alarm).
- **"localhost" on Windows** tries IPv6 first (~2 s per request), so the tools use 127.0.0.1.

## 2026-10-31: performance modes and hardware test

- **Models:** exported in 12 variants (n/s/m × 640/960 × batch 1/4), `tools/export_models.py`.
- **Modes:** Light / Auto / HYPER presets (`core/performance.py`). The hardware test
  (`core/hwtest.py`, `tools/hardware_test.py`, Settings → Performance) measures each preset on the
  real engine and recommends with headroom (more when vMix shares the PC).
- **This PC (TensorRT, batch of 4, including image preparation):**

  | Model | Images/s |
  |---|---|
  | n @ 640 | 309 |
  | s @ 640 | 295 |
  | m @ 640 | 253 |
  | m @ 960 | 123 |

  **Recommended: AUTO running "high"** (m @ 640). HYPER fits 2 cameras, not 4.
- **Faster image preparation:** 22.5 → 17.2 ms per batch of 4 on CUDA (+30%). Measured three
  alternatives; OpenCV's blobFromImage was slower and was rejected.

## 2026-10-31: Phase 3, Canon-style web UI (core done)

- **Cameras view:** camera list, 2×2 grid (double-click for one large camera), live picture with
  person boxes and target brackets, click a person to track them, Auto Tracking per camera,
  tracking panel (composition, auto zoom, halting area), PTZ pad (hold to move), presets, STOP ALL.
- **Bottom bar like vMix:** Statistics button, detection time, GPU and CPU (whole PC and this app),
  problem lights.
- **Statistics page:** vMix-like table per camera (source fps, arrival gaps, source dropped, detector
  skipped, detection time, frame → result, VISCA round trip, FreeD, targets lost / re-found / watchdog
  stops), PC panel, live graphs, problems, log file view, Reset.
- **Settings (Canon web-settings style):**
  - **Cameras:** "Find devices" finds NDI sources and BirdDog cameras.
  - **Performance:** mode and hardware test.
  - **Advanced:** certainty thresholds, live; locked until "Advanced mode" is on.
  - **System.**
- **Security:** a WebSocket and POST commands from other web sites are refused (Origin check).
- **Checks:** checked in the browser pane against the virtual camera; svelte-check reports no errors.
  `npm audit`: one low-severity issue in a build-only dependency (not shipped).

## 2026-11-01: Phase 4a, Canon's App Settings, areas and setting wizard

**Built**
- **App Settings:** 5 named slots per camera with every Canon setting and range
  (`core/tracking_settings.py`); live changes, kept on disk after Save; Initialization (one slot, or
  all slots and the preset links).
- **Areas, stored in room angles** (they stay on the room while the camera moves; `rules/`):
  - shooting range (the picture's edges stay inside), initial position
  - priority display area (kept in the picture; limits zoom-in), fixed viewing angle areas 1-4 (the
    shot captured with the area is held while the target is inside, with a margin so it doesn't
    flap at the edge)
  - auto select area with start time, exclusion areas 1-4, tracking halting areas 1-4
  - Canon's precedence rules and its consistency checks ("outside the shooting range", "can't be
    used together", "outside the initial position's view") shown on the pages
- **Tracking behaviour** (`core/session.py`): Canon's statuses (No target selected, Searching,
  Tracking, Angle Fixed, Target lost); start from the initial position; when lost: keep the view or
  return to the initial position after the set time; fix tilt; sensitivity 1-10; Display Size (zoom
  once) and Auto Zoom (keep zooming).
- **Manual operation:** a camera moved by someone else (vMix, joystick, BirdUI) is noticed from its
  position while we send nothing. "Restart Tracking After Manual Operation" off: tracking stops; on:
  it waits until the camera is still, then continues. This app's own PTZ buttons, Click Centering
  and View follow the same rule.
- **Camera Preset Link** (Phase 4d, done early because it shares the move logic): On = tracking
  starts with the chosen slot once the camera is there; Off = tracking stops; blank = manual operation.
- **UI:** Tracking Execution panel (slot buttons, Auto Tracking, status, composition, Initialization,
  Save), mouse modes (Tracking Target Select, Click Centering, Silhouette Manipulation, Set Area),
  areas drawn in Canon's colours and dragged/resized on the picture, the silhouette, people in
  halting/exclusion areas dimmed, auto-select countdown; Setting Wizard and settings pages (steps
  1-7 and 10; 8 and 9 come with Phases 4b/4c) with pan/tilt/zoom sliders around the picture. Works
  at phone width.

**Checked end to end in the simulator** (`tools/sim_scenarios.py`, all PASS): auto select ignores the
exclusion area; the fixed area holds its shot exactly; walking into a halting area lets the target
go, the camera returns home and picks them up again; the picture never passes the shooting range
edge (12.00° against a 12° limit); another controller moving the camera stops or pauses tracking;
preset link "On" starts tracking with App Settings 4 at the preset.

**Bugs found by these tests and fixed**
- **Absolute moves cancelled:** a stop command queued just before a move to a fixed angle could land
  after it and cancel it. Movement commands now go out one at a time, in order (`control/camera.py`).
- **Simulator:** the fake camera kept its old drive speed after an absolute move (a real camera
  stops); this looked like "someone else moved the camera".
- **Duplicate people:** at a 30° view the detector sometimes reported a second, nearly identical box
  (IoU 0.9+) for one person, which became a second track that could take over the target: only 58%
  target visibility. Fixed with standard NMS (IoU 0.7) and "a small box inside a tracked person is
  not a new person": **96-99%**, no losses.
- **Frame rate limit:** it waited for fixed time slots, which added up to a frame of delay and, with
  uneven NDI arrival, skipped a third of the frames (21 instead of 30 detections/s). Now a frame is
  analysed when it arrives or skipped (per-camera token bucket).
- **Light mode problem light:** "tracking 15 of 30 frames/s" would have been reported as a problem in
  Light mode, whose limit is 15/s; the expected rate now includes the mode's limit.

## 2026-11-01: Phase 4b, Auto Framing

- **Multi-Person** (`rules/framing.py`): people near the target join the shot after 0.5 s (leave
  after 1 s), nearest first, up to 4 people, while the view they need keeps the target at least the
  minimum size and the lens can show it; nobody joins while the target is near the picture's edge;
  people in halting/exclusion areas are never framed. The shot blends to the group's middle at the
  transition speed and zooms out (never tighter than the one-person shot); afterwards it zooms back.
- **Face Direction:** from the nose against the ears/eyes; smoothed by the sensitivity; the display
  position moves to leave space in front; "facing forward" keeps or centres it.
- **Sit/Stand:** from the knee drop against the torso length (keypoints), 0.7 s before a change
  counts; sitting and standing sizes; turning it on switches Display Size and Auto Zoom on (Canon).
- **UI:** wizard step 8, Auto Framing toggles on the tracking panel, group members marked on the
  picture, "Tracking N people", "facing left", "sitting".
- **Bugs found in the simulator and fixed:** the group transition was first fed to the motion filter
  as if the person moved fast, so the camera overshot to the other person and swung back and forth;
  now the person's own motion is followed and the group is an aim offset. The other person's track
  number changed while the camera swung, which reset the group; a new track where a member just
  vanished now keeps the place. With the smallest "minimum size" a group could need a wider view
  than the lens has, and the target walked out of the picture; now limited by the lens.
- **Checked:** 12 unit tests; scenario `multi`: the group is framed for 11.6 s with the camera at the
  middle (27.6° between people at 20° and 38°), back to one person afterwards, never lost.

## 2026-11-01: Phase 4c, Registered Face Tracking

- **Models:** OpenCV YuNet (face finder, 0.23 MB) and SFace (face fingerprint, 38.7 MB) from OpenCV's
  model zoo, `tools/get_face_models.py` (prints SHA-256). Same face at another size: similarity
  0.94; different people: 0.12; threshold 0.363 (OpenCV's). Under 50 ms per look on the CPU.
- **How it runs:** a thread in the vision worker looks at head crops (each person about twice a
  second, at most two people per frame), so detection never waits. Identities are attached to the
  tracks.
- **Faces:** stored only on this PC (`data/faces/`: names, fingerprints, 96 px thumbnails), shared by
  all cameras; each camera has its own list of up to 7 (order = priority, on/off), like Canon's.
  Registration: a frame on the picture with Canon's checks (one clear face, big enough, centred,
  facing the camera, not too dark/bright) turns green; Capture. Delete; add a face registered on
  another camera; Settings → System → Delete all face data.
- **Tracking with faces (Canon):** with Registered Face Tracking on, only switched-on registered
  faces are picked, the highest priority first, at once (no start time), also after a loss; a
  higher-priority face appearing doesn't take over. **Identity lock:** a lost target with a known
  face is re-found by face anywhere in the picture, and someone recognised as another person is
  never adopted as the target.
- **Simulator:** people are now drawn from their original photos at screen resolution (zoomed-in
  shots showed an 18-pixel blur before).
- **Checked:** 8 unit tests (one with the real models); scenario `faces`: the speaker's face is
  registered at the podium, then picked by face from a 45° view; at 40° the face was recognised in
  10 of 10 looks (similarity 0.84-0.86).
- **Not done (later):** body re-identification for people seen from behind (needs another model).

## 2026-11-01: Phase 5, on-air motion and audio-follow

- **vMix tally** (`integrations/vmix.py` VmixLink): one connection follows tally, a second re-reads
  the input list every 10 s (which also notices a dead connection); reconnects every 3 s. Each camera
  is tied to a vMix input by its key; the input showing the camera's NDI source is suggested.
- **On-air motion** (per App Settings, wizard step 5): while the camera is on Program its top speed,
  acceleration and zoom are reduced and its Pan/Tilt Halting Area grows; blended over a second both
  ways. New targets, return-to-initial and auto picks wait until off Program (or move slowly; a
  setting). Absolute moves (fixed areas) use the slower speed. A manual Auto / On / Off switch per
  camera, also for use without vMix (and from Companion through the HTTP command API).
- **Audio-follow** (`rules/audio.py`, `integrations/xair.py` MixerLink): MR18 / X Air levels (about 20 a
  second), the mixer's channel names, mutes and faders. Speech = above the threshold for the attack
  time, ended after the hold time; muted mics and faders down never speak. Each mic: a label, the
  registered face of the person using it, a preset per camera; the list order is the priority. An
  off-air camera answers first; if only the live camera could, it waits (or takes the visible person
  over slowly); a camera the operator used recently is left alone; a camera already on that person
  isn't moved. The camera goes to the preset, then frames the person with that face, or else the
  person nearest the display position.
- **UI:** Settings → "vMix & mixer" (addresses, status, camera ↔ input with suggestions, find mixers)
  and "Audio follow" (live meters with the threshold, mic mappings, priority); tracking panel: on-air
  state, Auto/On/Off, audio-follow switch; camera tiles get a red LIVE border.
- **Checked:** 8 unit tests (speech detection, choosing a camera, the vMix and mixer links against the
  fakes) and 3 session tests; scenarios `onair` (live top speed step 5 against 9 off air; tally seen
  both ways) and `audio` (mic 1 → preset → the person at the podium framed), with
  `tools/fake_devices.py --no-camera --vmix-input "<PC> (TRACKXEN SIM 1)" --cycle 8`.

## 2026-11-01: Phase 6, four cameras and robustness (in progress)

- **Applying settings without stopping the cameras:** if the camera setup itself didn't change (for
  example only the performance mode), only the vision worker restarts; cameras keep tracking.
- **Statistics history** (Statistics → History): pick a day, see its CPU / GPU / detections per
  second / frame-to-result graphs and its events (filter by level, search), download the text log,
  events and samples. Read back from the daily log files (`core/logview.py`).
- **Show files** (Settings → System): save the whole setup to one file per venue and load it there
  (registered faces stay on the PC; faces a show file names but this PC doesn't have are left out).
- **Four cameras:** frames from unsynchronised cameras mostly arrive one at a time; they were padded
  to the 4-image model with blank images. Now a single-frame model handles lone frames and the
  4-image model several at once. Simulator: `tools/sim_camera.py --camera k` (k = 1-4) and
  `python -m trackxen --sim --sim-cameras 4`; `tools/sim_load.py` runs all cameras tracking at once
  for minutes or hours (load and soak test) and reports what was measured.
- **Preview pictures** are made on their own threads (with 4 cameras ~48 JPEGs a second).
- **Measured, 4 cameras tracking for 5 minutes** (this PC, normal preset):

  | Camera | On target | Detections/s | Detect ms p50/p95 | Frame → result ms p50/p95 |
  |---|---|---|---|---|
  | cam1 | 98 % | 28 | 7.8 / 8.6 | 32 / 46 |
  | cam2 | 98 % | 28 | 7.4 / 8.4 | 32 / 47 |
  | cam3 | 98 % | 28 | 7.4 / 7.7 | 31 / 32 |
  | cam4 | 98 % | 29 | 6.6 / 7.5 | 31 / 46 |

  CPU 2 % (32 threads; Windows' own counter agreed), GPU 39 % (95th 44 %), TrackXen memory 2.0 GB from
  start to end (no growth), no worker restarts, no warnings or errors in the log. After moving the
  previews off the detection thread the 95th percentile of frame → result dropped to 32 ms; the median
  stays 31 ms because the detection thread is ~90 % busy (question 10).
- **Problem lights with hysteresis:** while TensorRT was still being built for 4 cameras (CUDA is
  slower), "tracking 23 of 30 frames/s" came and went every second, flooding the log and blinking the
  light. A problem now shows after lasting 3 s and clears after 5 s without it.
- **Soak test, 30 minutes, 4 cameras, on the built TrackXen.exe** (`tools/sim_load.py`): ready after
  315 s (first start: TensorRT built for 4 cameras); every camera on its target **100 %** of the time,
  28-29 detections/s each, detection 6.3-7.7 ms, frame → result 16-31 ms; CPU 1 %, GPU 38 %; memory
  3.86 GB at the start, 3.88 GB at the end (highest 3.90 GB: no leak); the vision worker never went
  down; the log had six one-second "21-23 of 30 frames/s" dips, which the new hysteresis now ignores.
  (The built program uses more memory than the development run, 3.9 against 2.0 GB, probably because
  it built the TensorRT engines itself in that run; to look at later.)
- **Still to do:** a service-length soak test (several hours) on the real cameras.

## 2026-11-01: Phase 7, the Windows program and installer

- **TrackXen.exe** (`packaging/trackxen.spec`, PyInstaller, one folder): the engine and web UI run in
  the background with an icon in the notification area (Open TrackXen, Open the logs folder, Quit);
  the UI opens in the browser; starting it again just opens the UI. Settings, logs, faces and the
  TensorRT engines go to `%APPDATA%\TrackXen` (Program Files isn't writable). About 4.8 GB, mostly
  NVIDIA's libraries, which keep their package layout so ONNX Runtime finds them.
- **Tested from `dist\TrackXen`** against the simulator, with its own data folder: the vision worker
  starts on CUDA, builds the TensorRT engine for this GPU on the first start (about 4 minutes) and
  switches to it (6.9 ms per detection, 29 detections/s); tracking (target visible 176 of 185 samples,
  median framing error 1.9 %); face models load; the hardware test runs and recommends a mode.
- **Bug found and fixed while packaging:** the hardware test took its test picture from the
  Ultralytics package, which the installed app doesn't contain; the picture now ships with the app.
- **First-run screen** (no cameras yet): this PC's graphics card, driver and engine, the TensorRT
  preparation with elapsed time, a quick speed test with "Use it", Find cameras with checkboxes and
  "Add cameras", and a link to vMix & mixer. Tested with an empty setup: the simulator's camera was
  found with its IP and added; the camera view appeared with live video.
- **Installer** (`packaging/trackxen.iss`, Inno Setup 6): Program Files, Start menu and optional desktop
  shortcut, optional start with Windows, a firewall rule for tablets (private networks), a warning
  when no NVIDIA card is found, licence notice (`LICENSE-NOTICE.txt`). Not compiled yet: Inno Setup
  isn't installed (question 7). `packaging/build.ps1` builds the UI, the program and the installer.
- Test switches: `TrackXen.exe --no-browser`; `TRACKXEN_DATA=<folder>` uses another data folder
  (also a portable copy).

## 2026-11-01: Phase 8 (started): video from HDMI/SDI capture cards

- Settings → Cameras → "Video from": NDI or **HDMI / SDI capture card** (Windows DirectShow device
  number; "Find capture devices" lists the ones that open). Control still goes over the network.
  Asks for 1080p30; re-opens the device when it is unplugged and plugged in again.
- **Not tested with a real capture card:** I didn't open this PC's video devices at night (that could
  switch on a webcam). One unit test checks the source choice. To try: pick the capture card in
  Settings → Cameras, Apply, and look at the picture and the Statistics page.

## 2026-11-01: work from the answers to questions.md

**Speed-ups (questions 5 and 10).** Measured first: a batch of 4 at 960 px took 32.4 ms. That was
8.0 ms of image preparation on the CPU, 22.0 ms in ONNX Runtime (including copying 44 MB of float
pictures to the GPU) and 0.3 ms decoding. Converting each NDI frame to BGR took another 6-7 ms
(numpy's strided copy). Three changes:
- **Image preparation in the model:** `tools/fuse_preprocess.py` puts the steps into each model, so
  they run on the GPU: uint8 pictures in, then to float, scale, BGR → RGB and channels-first.
  - The CPU only resizes each frame into a reused, padded input buffer (`PoseDetector.prepare`), so a
    quarter of the data goes to the GPU.
  - A test checks that the patched model finds the same people as the original, within 1 px.
- **Two-stage pipeline** (`vision/pipeline.py`): the GPU runs one batch on its own thread while the
  main loop decodes, tracks and reports the previous one and prepares the next.
  - While the GPU is busy, a new batch is only prepared once every camera has a fresh frame, so
    batches stay full.
  - The frame-rate limit decides once per frame.
  - The hardware test measures through the same pipeline.
- **NDI frame conversion** with `cv2.cvtColor`: 1.25 ms instead of 6.2 ms, with identical pixels.

Hardware test (4 cameras, TensorRT, batch of 4; images per second):

| Model | Before | After | One batch: prepare + GPU + decode, after |
|---|---|---|---|
| n @ 640 | 310 | 971 | 0.7 + 3.9 + 0.3 ms |
| s @ 640 | 299 | 875 | 0.8 + 4.4 + 0.3 ms |
| m @ 640 | 265 | 648 | 0.7 + 6.1 + 0.4 ms |
| m @ 960 (HYPER) | 123 | 326 | 1.2 + 12.2 + 0.4 ms |

HYPER needs 168 images/s for 4 cameras at 30 fps with headroom. It now fits (1.9×), and the test
recommends HYPER for 4 cameras on this PC.

4 virtual cameras tracking, 2 minutes each. "Before" ran the code of commit 6e4c909 from a separate
worktree against the same virtual cameras:

| Mode | | Detections/s per camera | Frame → result p50 / p95 | Skipped/s | Target visible |
|---|---|---|---|---|---|
| HYPER (m @ 960) | before | 19.7 | 78 / 78 ms | 10.5 | 98-99 % |
| | after | 30.1 | 15-16 / 16-31 ms | 0 | 99 % |
| Auto (m @ 640) | before | 28.1-28.5 | 32-46 / 47 ms | 1.9 | 97-100 % |
| | after | 30.1 | 15-16 / 15-16 ms | 0 | 99 % |

GPU load in HYPER went from 67 % to 56 %. TrackXen's memory was 3.8-3.9 GB before and after.

**Found while checking these numbers:** on Windows, Python 3.12's `time.monotonic()` only ticks every
15.6 ms, so every frame → result time above is rounded to whole ticks (0, 15.6, 31, 47, 63, 78 ms).
That includes last night's "31 ms". They are real readings, but coarse. Now fixed: the frame →
result time, the frame arrival gaps and the control loop are measured with `time.perf_counter()`
(0.0001 ms resolution). The detection, VISCA and hardware-test timings already used it.

Exact frame → result after the fix, 4 virtual cameras, 2 minutes each:
- **HYPER:** p50 9.3-17.0 ms, p95 9.8-18.3 ms.
- **Auto (m @ 640):** p50 8.0-12.9 ms, p95 8.5-14.1 ms.
- **Both:** 30.1 detections/s per camera, no skipped frames, target visible 99-100 %.

The two cameras whose frames arrive first in each round wait about 5-8 ms longer: the GPU stage
waits for all four cameras' frames to fill a batch. TrackXen used 2.1 GB of memory in these runs,
with no TensorRT build in that process. The runs that built TensorRT engines in the same process
stayed at 3.9 GB, which probably explains the higher memory of the built program after its first
start.

The built program, tested end to end (`dist\TrackXen`, own data and library folders, the 4 virtual
cameras):
1. It started with no NVIDIA libraries and downloaded the 22 files for this RTX 4080 SUPER: 1,534 MB
   in 49 s, every file checked.
2. Detection ran on the CPU meanwhile, about 4 frames/s per camera; the problem lights said so.
3. The vision worker restarted on CUDA, and TensorRT built its engines (8.5 min the first time). The
   builder file for this GPU generation was enough; the generic "ptx" one isn't needed.
4. Tracking: 30.1 detections/s per camera, 97-98 % visible, no problems.

The libraries take 2.2 GB on disk.

**Setup program** (Inno Setup 6.7.3, installed with winget for this user):
- `installer-output\TrackXen-Setup-0.1.0.exe`: one file of 611 MB.
- Tested: a silent "only for me" install without administrator rights, 1,227 files (1,000 MB); the
  firewall step is skipped as intended.
- The installed copy found the already-downloaded libraries and ran on TensorRT 2 s after start, and
  tracked.
- The silent uninstall removed it.

**PIN (question 1).** An option in Settings → System, off by default (`core/access.py`).
- **Storage:** the PIN is a PBKDF2 hash in `access.json`, outside settings.json, so it is never in the
  settings API or a show file. Device sign-ins are random tokens in HttpOnly SameSite=Strict cookies,
  stored hashed.
- **Who needs it:** this PC never asks (unless ticked). Companion sends the PIN in an
  `X-TrackXen-PIN` header.
- **Wrong tries:** after 5 wrong PINs an address waits 30 s, doubling up to 15 min; every wrong PIN is
  logged.
- **Tests:** 11 in `test_access.py`. Also tried against the running app from the LAN address: 401
  without the PIN, a wrong PIN refused, the right PIN signs in, and the header works. The PIN screen
  and the settings were checked in the browser.

**NVIDIA libraries downloaded on first start (question 8)** (`core/gpu_libs.py`).
- **Size:** the 31 NVIDIA DLLs were 4.0 GB of the 4.8 GB program; the program is now 998 MB.
- **What a PC downloads:** only the DLLs it needs, straight out of NVIDIA's wheels, with one HTTP range
  request per file. CUDA and cuDNN come from files.pythonhosted.org, TensorRT from pypi.nvidia.com.
  - Every PC: 1.37 GB.
  - One TensorRT builder file for the PC's GPU generation: 161 MB for an RTX 40, 94-425 MB for others.
- **Checks:** each file against its SHA-256 from the build. Finished files survive an interruption;
  a failure is retried after 2, 5 and 15 s, and the whole download every minute.
- **Until it finishes:** detection runs on the CPU. Then the vision worker restarts on the GPU.
- **The list:** `tools/gpu_libs_manifest.py` writes `core/assets/gpu-libs.json`, and the build checks
  it against the installed packages.
  - Found while writing it: the installed TensorRT package's RECORD lists sizes and hashes from before
    NVIDIA signed the DLLs (10 KB smaller). The real files are byte-identical to NVIDIA's (same size
    and CRC), so the hashes come from the files themselves.
- **Found in the build:** PyInstaller had pulled PyTorch's own cuDNN into the program while looking for
  the libraries left out. Excluded; the build now fails if any NVIDIA DLL gets in.
- **Tests:** 7 in `test_gpu_libs.py`, with a local range-request server.

**Dependencies.** `pyproject.toml` lists the runtime packages; FastAPI, Starlette, Uvicorn, websockets
and pywin32 had been missing from every requirements file. `requirements-dev.txt` installs the
project with its tools. The simulator and the tests use the shipped sample photo, so PyTorch and
Ultralytics are only needed to export models again.

## 2026-11-01: PTZOptics G2 cameras (PT20X-SDI-GY-G2)

The research is in research.md 1.9.

**Built**
- **Camera types** (`core/camera_models.py`): BirdDog P400, PTZOptics G2, other VISCA camera. A type
  fills in its VISCA connection, FreeD, units per degree (pan and tilt separately), speed tables,
  lens and video delay. PTZOptics values come from the data sheet and are marked as estimates until
  measured.
- **VISCA** (`devices/visca.py`): "sony" (header, UDP 52381), "udp" (plain, PTZOptics 1259; the default,
  works next to vMix), "tcp" (plain, 5678, with reconnect).
  - Speeds are kept within each camera's range (PTZOptics tilt ≤ 20).
  - Cameras without FreeD are polled 20 times a second.
- **Network video** (`vision/sources.py` `StreamSource`, PyAV 19.0.2):
  - RTSP over TCP or UDP, FFmpeg low-delay options, slice threads only, the newest frame kept,
    reconnects
  - measured: decode time, damaged frames, gaps
  - the default address `rtsp://IP:554/1`
- **UI:**
  - **Settings → Cameras:** camera type, "Video from: the camera's network stream", the stream
    address with Main/Sub stream, VISCA connection, and **Test camera** (VISCA position and round
    trip; stream size, codec, fps, decode time).
  - **Welcome page:** "Cameras without NDI": add by IP address.
  - **Statistics:** stream decode time; position source FreeD or VISCA polls/s.
- **Lab tools:**
  - `visca_probe.py`: `--transport udp|tcp`; `calibrate` (units per degree from the end stops);
    `zoom-range`; speed sweep in degrees without FreeD.
  - `stream_probe.py`: stream measurements and the **video delay** (pans the camera, compares the
    polled position with the picture).
- **Simulator:** `sim_camera.py --ptzoptics` behaves like a PTZOptics: plain VISCA, no FreeD, the data
  sheet speeds, and an error for tilt speeds above 20. `python -m trackxen --sim --sim-cameras 4
  --sim-ptzoptics 2` sets cameras 3 and 4 up that way.

**Tested**
- **Unit tests:** 10 in `test_ptzoptics.py`: types, plain UDP/TCP, late completions, polling and speed
  limits, the stream source with an H.264 video made by PyAV, a stream that isn't there.
- **All 10 simulator scenarios pass with the camera in PTZOptics mode.** That includes "manual":
  another controller moving the camera is noticed from the polled positions, without FreeD.
- **Mixed 4-camera run (2 P400 + 2 PTZOptics), first try:** 73 mixed-up VISCA replies in 2 minutes →
  the bug above, fixed and covered by a test. Second try: no VISCA errors; position polled 21 times a
  second per PTZOptics camera.
  - Its speed figures (19.5 detections/s, frame → result 108 ms) are **not comparable**: a game was
    using ~70 % of the GPU at the time. The simulator was stopped to leave it the GPU.
- **Lab tools against a pretend PTZOptics:**
  - `calibrate` found the ends at ±2448 = 14.4 units/degree
  - `zoom-range` 0..16384
  - the tilt sweep reproduced the speed table (steps 18-20: 47, 57, 70 °/s)
  - `stream_probe` measured a test H.264 stream
- **Also found and fixed:**
  - `calibrate` drove the tilt at speed 23, which a PTZOptics refuses
  - `--yes` only worked before the command name
  - the scenario tool always used the P400's VISCA format

**Waiting for the real camera** (with someone there, it moves):
- whether VISCA on UDP 1259 answers like the documentation says
- `calibrate`, `zoom-range`, the speed sweeps
- `stream_probe --delay` (the RTSP delay)
- main or sub stream

## 2026-11-02: fixes from a code review

A code review listed defects across the engine, the UI, the tools and the installer. They were
fixed in ten groups (session, control, devices, vision, ops, server, lifecycle, app, UI, tools and
docs) and merged here. Where a test could show a defect, it was written first and failed on the old
code. The suite went from 158 to 383 tests (394 with the fixes from a second review, 403 with a
third, 411 with a fourth).

**Safety: the watchdog and the control loop keep running**
- **Watchdog:** each check runs inside try/except. An error is logged (the same one at most every
  10 s) and the watchdog carries on. Before, one camera that didn't answer the stop ended the
  watchdog for good: no more vision restarts and no more no-video stops.
  - A crashed vision worker's restart is scheduled before any camera is stopped, and the cameras are
    stopped all at once.
  - The restart waits 2 s, doubling up to 60 s while the worker keeps crashing, and back to 2 s once
    a worker has run for 60 s. The log says "restarting in N s".
- **Control loop:** a camera whose control step fails is sent a stop, and the error is logged. The
  other cameras are still controlled. Before, one error (for example `math.log(0)` from a person
  height of 0) stopped the loop for every camera.
- **Applying settings** no longer freezes camera control while the old vision worker exits (with a
  worker that took 1 s to exit: a 1.02 s stall before, under 0.3 s now). Applies run one at a time.
  Commands sent during an apply change the new settings.
  - Found in a third review: camera commands (App Settings 1-5, vMix input, preset links reset, face
    delete) sent while an apply closed the old cameras were answered ok but lost. They are kept now,
    also in settings.json. A show file still replaces everything.
  - Found in a fourth review: when that second save failed (a full disk, a file held open on
    Windows), the apply stopped halfway: old cameras closed, no vision worker, so no camera could be
    moved or tracked until a restart, and no problem light. It now logs a "settings" error and goes
    on; the new settings were saved before the cameras were closed.
- **Log writes:** a full disk, a deleted logs folder or a locked file no longer raises out of the log.
  Before, that could end the control loop with a camera moving. A "pc" warning says the log files
  can't be written, and a deleted folder is made again.
- **Ctrl+C or SIGTERM** (`python -m trackxen`) now lets App.stop() finish: cameras stopped, worker
  ended, exit code 0. A second Ctrl+C while stopping is ignored. Before, SIGTERM killed the process
  without stopping anything.

**Safety: STOP ALL holds**
- **STOP ALL** now, on every camera: Auto Tracking off, pending moves and microphone cues dropped,
  and **audio-follow off**. Mics waiting for a free camera are forgotten. All cameras are stopped at
  the same time, so one that doesn't answer doesn't hold up the others (tested: the second camera
  stopped within 0.15 s while the first took 1 s and failed). Log: "STOP ALL pressed: tracking and
  audio-follow off on every camera". The operator switches audio-follow back on per camera.
- Before, the next microphone could recall a preset and start tracking again right after STOP ALL.
- **During a settings apply:** each camera's Audio follow and On Air switches carry over to the
  cameras the apply makes again. A STOP ALL pressed meanwhile is no longer undone, also for cameras
  made after it (found while merging).
- **In the UI:** while the live connection reconnects, STOP ALL goes over HTTP. If it can't reach
  TrackXen, the page says "STOP ALL didn't reach TrackXen (...): press it again".
  - Found in a second review: a STOP ALL sent on a live connection that then dropped waited 5 s and
    was never sent over HTTP, and on a connection dead without closing every press went into it. Now a
    stop without an answer (the connection closed meanwhile: at once; no answer: after 5 s) is sent
    again over HTTP. The same goes for the hold-to-move stop. Tested in node on the real store
    (`tests/test_ui_live.py`, skipped without node and the UI's packages).
- **Auto Tracking off** also cancels a pending "start tracking at the preset" (Camera Preset Link On,
  or an audio cue) and a microphone cue still waiting for its person.

**Safety: a camera left moving**
- **Failed commands:** when an absolute move, a preset recall or a stop fails (or its answer is
  lost), the speeds the camera runs at count as unknown, so the stop is sent again. Before, a drive
  whose ACK was lost kept the camera turning at 11.5 °/s in the test.
  - Found in a third review: quitting TrackXen, or removing a camera in Settings, sent its stop only
    once, so a lost or refused stop left the camera turning and zooming with nothing left to stop it.
    That stop is now sent again for 1.5 s after a failed try, at most every 0.1 s. Quitting also closed
    the cameras one after another once the control loop had ended, so each camera that didn't answer
    kept the next one moving 0.5-1.5 s longer. All cameras are now stopped at once, and vMix and the
    mixer are closed first, so no microphone starts a camera meanwhile. With a camera that doesn't
    answer, quitting takes up to about 3.5 s.
  - Found in a fourth review: the third review's 3 tries went out within a millisecond, so a camera
    busy for a moment ("command buffer full") refused all of them, and a TCP connection that had just
    dropped failed all of them at once. Hence the 1.5 s above; a dropped TCP connection is made again
    for the stop. A camera that never takes it is logged: "stop not acknowledged on closing, the
    camera may still be moving". Before, nothing was logged.
- **Slow corrections:** an axis whose wanted speed rounds to step 0 now settles inside the dead zone,
  or moves at the slowest step outside it. Before, it could stay "moving" at step 0 for good, which
  also blocked Display Size and Auto Zoom (46 of 288 simulated latency cases; 0 now).
  - Found in a second review: with the Halting Area off (or a very narrow one) on a tight shot, the
    slowest step coasted past the tiny dead zone during the camera's delay, so the camera started and
    stopped 2-5 times a second around a person standing still. It now also settles within the
    distance the slowest step coasts in the lookahead time (0.12 s; about 0.2° on a PTZOptics G2).
- **Old positions:** a position older than 2 s (FreeD and VISCA inquiries both stopped) counts as
  unknown. Tracking sends stops instead of steering by a frozen position.
- **Acceleration limit:** two commands at the same moment no longer skip it.
- **After a watchdog "no video" stop** the camera accelerates from standstill again.
- **Hold-to-move buttons** stop the camera when the button is let go or removed (panel closed, PIN
  screen), when the tab is hidden or loses focus, and when the connection drops (the stop then goes
  over HTTP).
- **Reconnects:** a reconnect no longer cancels a move to a preset or position under way; a drive is
  sent again on the new connection. Only one reconnect runs at a time, and closing a camera during
  one leaves no connection open.
- **Camera connections that can't be opened at start** (camera off, name not resolvable yet, FreeD
  port taken, a typo in the address) no longer stop TrackXen. The camera's loops start anyway and
  reconnect; the app tries what is still missing again after 5 s, then less often up to 60 s.
  - Found while merging: the app's retries closed the connection the camera link had made meanwhile
    and started another pair of loops each time, so the camera flapped between "answering" and
    "not answering". A retry now only opens what is missing (the FreeD port).

**Safety: NDI freezes**
- `find_sources()` and `NdiReceiver.start()` no longer wait inside the NDI library with the GIL held.
  A 1 s search used to pause every other thread for 1.0 s; now about 5 ms.
- **A missing NDI or capture source** is opened on its own thread, so the other cameras' detection
  goes on. Before, one missing source held up detection for every camera for 0.5 s every 5 s, and the
  watchdog stopped the tracking cameras. It is tried again after 5 s, doubling up to 60 s.
- **NDI names:** a full "HOST (STREAM)" name is matched only exactly (case does not matter), so
  "ALTAR (CAM)" no longer takes "SIDEALTAR (CAM)". A partial name that matches several sources is
  refused with "NDI name X matches several sources: A, B (choose one of them)".
  - Found in a second review: a partial name still took the only source containing it when its own
    wasn't announced yet ("CAM1" took "PC (CAM10)" while camera 1 was booting). A partial name now
    takes a source at once only when it is that source's whole host or stream name; a source that
    merely contains it ("BIRDDOG" in "BIRDDOG-1 (CAM)") is taken when the first search ends (2 s).
  - Found in a third review: a camera still booting after those 2 s (most PTZ cameras) still got
    "PC (CAM10)", until the next Apply. A part that splits a word of a source's name is now never
    taken: "CAM1" never takes "PC (CAM10)", and camera 1 connects on the next try once it is
    announced. A saved part that ends inside a word ("BIRDDOG-123" for "BIRDDOG-12345") no longer
    connects: "NDI source not found" in the log; choose the full name from the list.
- Other event-loop stalls removed: host names are looked up off the loop, the stats day file is read
  in pieces (worst loop lateness 1456 ms → 6 ms on a 120 MB file), `/api/log` reads only the end of
  the log, and setting the PIN hashes off the loop.

**Security**
- **DNS rebinding:** the server answers only when opened by an IP address, localhost or this PC's
  own names. Anything else gets 403 "Open TrackXen by this PC's IP address or name.", and the live
  connection is closed. With the PIN on, this PC needs no PIN only when opened as localhost or
  127.0.0.1 (the tray does that).
- **Cross-site requests:** any `/api` request a browser marks as coming from another site gets 403,
  GET included (for example discovery). Companion and the tools send no such header.
- **Signing out** a device, "Sign out other devices", changing the PIN and switching it on now also
  close pages that are already open. They reconnect and show the PIN screen if they need it.
- **PIN tries** are counted under a lock before the slow comparison, so 20 PINs sent at once no longer
  get 20 tries. Saving `access.json` from two sign-ins at once no longer fails.
  - Found in a second review: that also counted a tool's burst of requests with the *right* header
    PIN against each other, so the 6th and later (STOP ALL among them) got 401. Header PINs from one
    address are now checked one at a time, and the rest of a burst uses the first answer.
- **Face delete** takes only registered face ids, so a request can't name another file.
- **Escaping:** the `/test` page escapes camera names and problem texts. The text log writes line
  breaks and control characters in names as escapes, so a name can't forge a log line.
- **Stream addresses:** errors in the log and the UI never show the user name or password of an RTSP
  address.

**Settings**
- **Checked before they are applied** (HTTP 422, nothing changes): ports must be 1-65535 (FreeD may be
  empty); camera ids must be unique; two cameras can't share a FreeD port; composition values within
  their ranges; lens, speed-table and units-per-degree values that would crash; no NaN or infinite
  numbers. Found while merging: a NaN in the settings sent made the 422 answer itself fail with a 500.
- **settings.json** is written to a temporary file, flushed and then swapped in; the previous copy is
  kept as `settings.json.bak`. A damaged file (power cut, hand edit) no longer stops TrackXen: it is
  kept as `settings.json.broken-<time>`, and the last good copy or the defaults are used. Values no
  longer accepted go back to their defaults (a duplicate camera id gets the next free `camN`, a shared
  FreeD port is set to none). Each repair is a "settings" warning in the log.
- **Settings changed on another device:** `GET /api/config` carries a revision (`_rev`). Applying an
  older copy answers 409 "The settings were changed on another device. Reload them and try again.",
  and the page loads the current settings. Tools that send no `_rev` are not checked. Show files and
  settings.json never contain it.
  - Found in a second review: a page that opened Settings during another device's Apply got the new
    settings with the old revision, so its own Apply was wrongly refused. The revision now changes
    together with the settings. It also starts at a random number, so a page loaded before a restart
    isn't taken for current after it.
- **Kept over an Apply:** Advanced mode detection thresholds changed live, and each camera's Audio
  follow and On Air switches (both still reset when TrackXen restarts).
- **Show files** keep this PC's performance mode, hardware-test result, model override and engines,
  as they already kept its web address. Mic mappings to faces this PC doesn't have are cleared.
  - Found in a third review: a show file saved by an earlier version with a camera id or FreeD port
    used twice (the old "Add camera" after a removal) or a port of 0 (an emptied field) was refused
    with 422, so that venue's file couldn't be loaded at all. Those are now repaired as in
    settings.json; each repair is a "settings" warning in the log and is listed in the message after
    loading. Other values that are not valid are still refused, and Apply is as strict as before.
- **Deleting or wiping faces** also removes them from the audio-follow mic mappings. An audio settings
  page loaded before the delete can't put them back (found while merging).
- **UI:** "Add camera" takes the first free `camN` and a free FreeD port; changing a camera's type
  keeps its FreeD port. Emptied or out-of-range ports, shared FreeD ports and duplicate ids are
  refused with a message before anything is sent. Settings reloads each time it opens, unless this
  device has changes not applied yet.
- **Companion and the API:** on/off values must be true or false (1/0 and "true"/"false" are also
  taken); anything else is refused. Before, `"false"` switched tracking on. Malformed commands get
  `{ok: false, error}` instead of an HTTP 500.

**Problem lights** (same hysteresis: shown after 3 s, cleared after 5 s)
- New: "camera not answering VISCA" (error, cameras with an address); "FreeD position data not
  arriving on UDP port N" (warning, cameras with a FreeD port, while VISCA answers); "vMix at X not
  connected, on-air tally unknown"; "mixer at X not connected: no levels arriving"; "log files can't
  be written".
- GPU load and GPU temperature are separate problems; a second GPU is "GPU 2". A problem that gets
  worse (warning to error) is logged once more.
- "Camera answering VISCA" also comes back from answered position polls, so a few lost inquiries no
  longer add up to "not answering".

**Tracking and vision**
- **After the vision worker restarts** (watchdog, Apply, NVIDIA download) the target is found again by
  its registered face or near where it was last seen, not by its number: track numbers start at 1 in
  a new worker. Boxes from the old worker disappear until new detections arrive.
- **Identity lock:** if the target's box starts being recognised as another registered face (the
  tracker swapped people), the camera holds and goes back to the locked person wherever their face
  appears.
- **Fixed viewing angle areas** no longer move the camera while the joystick is held or tracking is
  paused after a manual move.
- **Audio-follow:** the "left alone after a manual move" time also starts on a preset recall and when
  another controller (vMix, a joystick) moves the camera. A waiting cue is dropped when the operator
  clicks a person, uses the joystick or moves the camera. A camera that found the person at a mic
  stays "on that person" while it follows them.
- **Zoom:** a one-time Display Size ends at the zoom limit. After a Multi-Person group the zoom returns
  to the display size (without Auto Zoom) or ends where the controller stops, so a later hand zoom is
  not undone. A person cut off by the bottom of the picture is never zoomed into.
- **Advanced mode thresholds** do what their labels say: the detector threshold follows "Keep
  tracking down to", "Start tracking at" below "Sure detection" starts tracks, "seen 1 frame" reports
  a person at once, and the overlap setting applies to weak detections too. Defaults are unchanged.
- **People:** a speaker in front of a screen showing a large person can be picked up; a face at the
  edge of someone's head crop is no longer taken as theirs; track ids are kept when a stream's
  resolution changes.
- **Errors made visible:** damaged face model files ("face recognition unavailable: ..."), a stream
  without video ("no video in this stream", retried), raw pose model exports decoded correctly.
- **Pan/tilt sign:** `pan_sign`/`tilt_sign` -1 now also flip polled positions, and `visca_probe
  calibrate` no longer suggests -1 when positions count against the drive.
- **FreeD** from any address other than the camera's own is ignored and counted (`freed_foreign`).
- **VISCA replies:** an error about an earlier command is no longer taken for a new command's answer.
  A position reply of the wrong length is an error ("pan/tilt reply is not 8 nibbles: ...") instead of
  a wrong position.
- **Face registration:** "<name> registered" shows only once the face is stored, otherwise "No face
  was registered: try again". Cancel ends the capture. A capture under way carries over to a
  restarted vision worker, and the frame is switched off even while the connection is down.

**Pages**
- While another device applies settings, cameras that drop out of the list stay shown for up to 5 s,
  so pages no longer jump to the Welcome page or to camera 1.
- After Apply or "Load setup", every device reloads the cameras' App Settings. App Settings commands
  sent before the selected camera's settings have loaded are refused with a message instead of
  changing the camera shown before.
- The PIN screen says "TrackXen is not reachable: try again in a moment" instead of locking up. A
  refused audio-follow change is undone on screen. A failed "Add cameras" on the Welcome page says
  why. Choosing a vMix input no longer clears addresses typed but not applied.

**vMix and the mixer**
- The vMix link no longer stops for good after an unexpected error, a slow input-list answer no
  longer drops the tally, and renumbered inputs are evaluated again within about 10 s.
- **vMix not connected:** each camera keeps its last on-air state, including the calm on-air motion,
  until vMix reports again. With no vMix address set, cameras are off Program as before.
  - Found in a third review: an Apply that made the cameras again, or loading a show file, still put
    a live camera off air until vMix reported again, so audio-follow could send it to a preset at
    full speed. Its on-air state now carries over too.
- A mic waiting for the live camera is forgotten when audio-follow is switched off or the mixer
  stops sending levels.
  - Found in a fourth review: it was only forgotten at the next vMix tally change. A camera taken off
    air with the On Air switch could then be sent to that mic's preset, and start Auto Tracking there,
    in a quiet room once audio-follow was switched on again or the levels came back. Switching
    audio-follow off in Settings now forgets it at once, and who is speaking starts over with the
    first levels taken after 2 s or more without any (mixer silent, or audio-follow off).
- Found in a third review: switching Audio follow off for a camera (or audio-follow off in Settings)
  while a mic's cue was taking it to its preset left the cue's "start tracking there" in place, so
  the camera switched Auto Tracking on when it arrived and picked someone. That is dropped now too.
  Found in a fourth review: clicking a person during that move still left it; that drops it now too.
- A malformed mixer address is shown and retried. Without levels the mixer link reopens every 30 s.

**The program, logs and downloads**
- **TrackXen.exe:** a second start for the same data folder waits for the first and opens its page
  (lock file `trackxen.lock`). A web port used by another program, or an engine that can't start,
  shows a message with the reason and no tray icon. Quit works while cameras are still connecting.
- `--port N` is used for that run only and no longer saved.
- **Logs** older than `log_keep_days` are removed once a day while TrackXen runs.
- **Hardware test:** measures on the engine that really loads (a PC without NVIDIA is measured on the
  processor), with one frame per camera in a batch (lower but realistic numbers for 2-3 cameras), and
  a true 95th percentile. Its message says when the NVIDIA download failed.
- **NVIDIA libraries:** an interrupted download resumes where it stopped; GPUs older than Turing
  download nothing and run on the processor.
- **Statistics:** a removed camera no longer shows its last frame rate after Apply; "Source dropped"
  counts start over with each new vision worker.
- **Installer:** an upgrade replaces the "TrackXen" firewall rule and the `_internal` folder; the build
  rejects every NVIDIA DLL the download provides. pywin32 is a Windows-only dependency.
- **Tools:** `sim_test`, `sim_load` and `sim_scenarios` refuse cameras that aren't the simulator's
  unless `--yes` is given. `sim_test` always switches tracking off at the end, `sim_load` puts each
  camera's auto select back, and the `onair` scenario puts the vMix settings back. `visca_probe info`
  only reads.
  `env_check` fails when its ONNX Runtime check dies. `gpu_libs_manifest` refuses a list that lacks a
  package's DLLs. A test checks that the version is the same in every file.
- `docs/plan.md` marks what is not built (body re-ID, the update check, "Stop all cameras" in the tray,
  DirectML).

**Not changed** (owner decisions, or not measurable here)
- Whether only Save should write App Settings to disk (any save does now), and a way to discard
  unsaved changes.
- When a preset move counts as arrived, and the jump between FreeD and polled VISCA positions: both
  need measurements on the P400 and the G2.
- TensorRT engines stay in `%APPDATA%\TrackXen`; moving them forces a rebuild on every install.
- The group zoom speed steps (2 and 3 are the same under the motion-profile cap): a mapping choice.
- System sampling off the event loop, a hang check for the vision worker during a TensorRT build, and
  the hardware test during a TensorRT build: can't be checked without the Windows show PC.
- Removing old NVIDIA library folders; the installer's "start with Windows" under elevation.
- Names in the logs after a face wipe, and RTSP passwords in the settings and show files: privacy
  choices.
- STOP ALL on the PIN screen: the engine refuses commands from a device without the PIN.
- The registration frame stays on if a device dies mid-registration, until the next vision restart.
- Restarting only the vMix or mixer link whose address changed; the "(development)" version label.

**Checked end to end in the simulator** (one virtual camera, fake vMix and mixer, Light mode on a
4-core CPU with no GPU: 4-6 detections/s instead of 15). `sim_scenarios` and `sim_test` were run on the
code before these fixes and after them, alternating, with the same settings and models.
- **Passed on both, every time:** auto_select, fixed, range, manual, preset, onair and audio. `sim_test`
  kept the target in view on both, with similar framing errors.
- **Failed on both, in the same way:**
  - **halting** (5/9 before, 4/9 after): at that detection rate the speaker's track number breaks as
    they walk into the halting area, so the target is "lost" rather than "released".
  - **multi** (0/3 on both): the camera reaches pan 18-21, but the scenario needs more than 24.
  - **faces** (0/3 on both): the face guide stays on "face the camera".
  These failures come from the slow CPU detection, not from code that changed.
- **No regressions:** no scenario failed after the fixes that passed before.
- **Logs:** no errors or tracebacks after the fixes. Both versions stopped cleanly on Ctrl+C.
- **Not checked here:** the scenarios at the full detection rate on the show PC.

## 2026-11-04: smooth pan and tilt

The request: smooth pan and tilt are guaranteed, never jerky, with an adjustment for it; where the
cameras have no ready way to move smoothly, TrackXen is the middle layer that shapes the commands.

**How it was measured.** A closed-loop bench in virtual time, now `tools/motion_bench.py`. The real
session, controller, Kalman filter, camera link, VISCA, FreeD, tracker and detection-rate code follow a
simulated person 15 m away against a fake P400 and G2 with a 40 ms speed ramp of their own. The person
walks at 0.5 and 1.2 m/s, stops and goes, or stands, with head bob, sway, head turns, flickering ear
keypoints, looking down, and detector noise. Cases: 50 and 8 degrees, on and off air, Halting Area on
and off; means of 3 seeds. These are the simulator's numbers: the cameras' own ramps and speed tables
are still estimates.

**What the cameras offer** (manuals, web APIs and menus read)
- **P400:** no acceleration, ramp or smoothing setting in its user guide or REST API; the API sets only
  top speeds (pan 0-21, tilt 0-18, zoom 0-7). Its menu has Adaptive P/T (slower pan and tilt when
  zoomed in); whether that changes VISCA moves is unknown. BirdDog publishes no P400 VISCA list, so
  Sony's ramp curve and pan-tilt slow mode are unconfirmed: `visca_probe.py HOST ramp` now asks.
- **G2:** no acceleration or ramp setting in its menus, VISCA or CGI. SpeedByZoom most likely affects
  only the IR remote (PTZOptics' G3 manual says so).
- **Comparable trackers** keep a smoothness or acceleration control next to sensitivity and a dead band
  (Sony Auto Framing's Speed, PTZOptics' acceleration, Seervision's smoothness, TrackPilot's Style and
  Attack, OBSBOT's Lazy to Crazy). Canon has Sensitivity and the halting area only.
- So TrackXen does the smoothing, for tracking and for its own joystick.

**The measured causes**
- **Stop-start while walking (the main one).** A move ended as soon as the error was small, even while
  the person walked on. The camera caught up, stopped, waited until the person left the halting area,
  and started again hard: 17-57 starts a minute and 9-60 % of the walking time stopped, on both
  cameras, at every zoom, on and off air. The on-air profile did not cure it.
- **Flapping speed steps:** the nearest speed step was picked afresh every 20 ms, so a walker between
  two steps switched the camera back and forth (visible once the stop-start was gone).
- **Noisy feed-forward:** the Kalman filter's speed of the person, 0.24-0.47 deg/s RMS off (3-6 % of the
  picture per second at 8 degrees), went straight into the speed command.
- **The G2's position age:** a polled position is about 48 ms old when the command is worked out (P400
  FreeD: 4-10 ms): 2.7 % of the picture at 8 degrees and 1.2 m/s.
- **Halting Area off:** its dead zone (1 % of the picture, 0.045 degrees of tilt at 8 degrees) was
  smaller than a walking head's bob, so the tilt hunted: 40-210 starts a minute.
- **The face lost** (looking down at notes): the followed point jumped from the face to near the top
  of the box, about 23 px at 8 degrees.
- **A brisk walker lost in a tight shot:** the top speed is scaled with the zoom (hfov / 40). At 8
  degrees on air the cap, 3 deg/s, is below a 1.2 m/s walker at 15 m (4.6 deg/s), and the person was
  lost (180-240 % of the picture off, RMS); at 5 degrees off air too.
- **Not causes** (measured): the Kalman filter's noise settings (a "correct", smaller measurement noise
  is much worse), the lookahead, the detection rate (8-30 a second), a video delay 40 ms off, missed
  detections, and the VISCA pacing (one command at a time, at most 30 a second, as Sony asks).
- **Trade-offs, not faults:** gain and acceleration are not scaled with the zoom. A lower sensitivity
  already calms tight shots; zoom-scaled acceleration did not help (below).

**What changed** (before → after each change, measured when it was made; the bench table below has
them all together)
- **A move doesn't end while the person keeps walking** (their low-passed speed above 0.15 × the dead
  zone per second and above 0.3 deg/s); a standing person still settles. On its own, P400 at 50
  degrees, 1.2 m/s: 29.9 → 0 starts a minute, stopped 22 → 0 % of the walking time, acceleration 33.4 →
  11.3 % of the picture per s², framing error 3.25 → 0.68 % RMS. P400 at 8 degrees, 0.5 m/s: 41 → 0
  starts.
- **A camera ahead of a walking person waits for them** instead of ending the move: at Smoothness 10 on
  air a late soft start overshot, and the camera then stood still 24 % of the walking time. Now it waits
  at speed 0 with the move open and never turns back against the walk (in the test: 58 % → under 10 %
  stopped). Default stop-and-go unchanged (p95 16.9 → 16.8 %); the G2's slow walk: acceleration 37 → 25.
- **Speed step hysteresis:** the step is kept until the wish is past the middle to the next one by 0.25
  of the gap; stops and reversals are never held.
- **Feed-forward from a low-passed speed** (0.15 s at the default).
- **A minimum dead zone:** at least 0.15 degrees, and at least the slowest speed step × 0.27 s (a G2:
  0.46 degrees). P400 with the Halting Area off, 0.5 m/s at 8 degrees: tilt 204 → 11 starts a minute;
  standing 52 → 5.7. G2 with the Halting Area on, walking towards the camera: tilt reversals 15 → 1.9.
- **The control step uses the camera's position moved on to now** (the newest one, extrapolated with
  the camera's speed over 0.15 s, at most 0.1 s ahead). G2 at 8 degrees, 1.2 m/s: framing error 5.86 →
  3.91 %, step changes 3.5 → 2.7 a second.
- **Keeping up in tight shots:** the zoom-scaled top speed may rise to twice the person's speed (never
  above the motion profile's top speed). A 1.2 m/s walker at 8 degrees on air: lost (233 % P400, 240 %
  G2) → 3.6 / 3.7 %; at 5 degrees off air 181 → 5.5 %.
- **The face lost:** the fallback point goes where the face has been in the box (remembered over 1 s)
  instead of a fixed 10 % below its top. Tilt, 1.2 m/s at 8 degrees: 8.0 → 3.4 starts a minute,
  acceleration 21.6 → 14.4; standing with the Halting Area off: 5.7 → 1.0 starts a minute. Pan is the
  same.
- **Pan/Tilt Smoothness 1-10** (per App Settings, default 5; old settings and show files load with 5),
  next to Tracking Sensitivity in the UI. Factors on the camera's motion profile, under the on-air one:

  | Level | Speed low-pass | Step hysteresis | Acceleration | S-curve build-up | Joystick ramp |
  |---|---|---|---|---|---|
  | 1 | none | none | 45 deg/s² | none | none |
  | 5 | 0.15 s | 0.25 | 45 deg/s² | none | 0.22 s |
  | 10 | 0.4 s | 0.35 | 33.75 deg/s² | 0.3 s | 0.5 s |

  In between linearly (the S-curve from 6 up). Gain, top speeds, lookahead and the feed-forward share
  are unchanged. Measured before → 1 / 5 / 10: P400 at 8 degrees, 1.2 m/s, acceleration 125 → 62 / 48 /
  37 and step changes 4.5 → 2.4 / 1.1 / 0.7 a second, framing error 4.2 → 3.5 / 3.5 / 4.3 %; P400
  stop-and-go at 50 degrees, acceleration 25 → 19 / 18 / 14. At 10 a 1.2 m/s walker at 8 degrees on air
  is still framed (5.2 % P400, 6.5 % G2 RMS).
- **The joystick middle layer** (`control/shaper.py`): the app's arrow buttons (the `manual` command)
  ramp pan and tilt along an S-curve in deg/s through the camera's speed tables. Both axes take the same
  time, so a diagonal stays straight; a reversal passes through a stop; zoom is sent as it is. A change
  takes at most 0.5 s × (Smoothness − 1) / 9, less when it is small in picture terms.
  - **Release bound:** from the moment the release reaches the engine, the pan/tilt step is 0 within the
    ramp plus one 20 ms control tick: at once at 1, within 0.24 s at 5, 0.52 s at 10. If the repeats
    stop arriving (connection dropped), within 0.3 s plus the ramp plus 0.02 s after the last one (0.82
    s at 10). The send loop then sends it within 1/30 s plus the VISCA round trip.
- **Safety stops stay immediate:** `CameraSession.halt()` forgets the ramp and sends a stop at once, on
  Auto Tracking off, the watchdog ("No video"), STOP ALL, a failed control step and closing a camera,
  and before presets and absolute moves (a ramp's next drive command would cancel them). The STOP ALL
  and closing tests fail without it.
- **Tools:** `tools/motion_bench.py` (above; `--check` fails on the smoothness and framing limits) and
  `visca_probe.py HOST ramp` (asks for Sony's ramp curve and slow mode, read only).

**Found in review and fixed** (each with a test that failed first; numbers before → after the fix)
- **Display Size and Auto Zoom while a person walks:** following a walker in one movement kept the
  axis "moving" for the whole walk, and the zoom waits while the camera moves, so it waited for the
  walk to end: a person walking towards the camera grew out of the picture. Now only a camera catching
  up (error outside the dead zone) holds the zoom back. Walking towards the camera at 30 degrees with
  Auto Zoom (4 seeds), size off the target while walking: P400 30.6 → 11.6 %, largest 1.09 → 0.65 of
  the picture height; G2 17.5 → 11.9 % (0d953f9: 13.5 / 12.7 %).
- **No turning back inside the dead zone:** following with feed-forward, the camera eases to a stop a
  little past a person who stops (the video and command delays), and it turned back at almost every
  stop. A move now ends where turning back would start inside the dead zone; past it, it does come back
  (tight shots). Stop-and-go at 50 degrees, 8 seeds: reversals 10.8 → 0 a minute (P400) and 10.3 → 0
  (G2), at Smoothness 1 too; at rest 1.03 → 0.49 s after the stop (0d953f9: 0.52 s). The picture then
  rests about 3 % of its width past the person, inside the halting area: framing error 2.28 → 2.72 %
  RMS (0d953f9 2.86), p95 5.14 → 5.08 %. At 8 degrees nothing changes. Every other bench case is the
  same or within 0.1 %.
- **Shooting range:** the camera followed a walker up to the limit and coasted past it; the overrun was
  inside the dead zone, so the picture rested past the limit for as long as the person was beyond it
  (24 degrees: 34-46 % of the run). A camera past the limit now comes back inside: at rest past it 0 %
  of the run (12 runs), resting edges 11.66-11.98 degrees for a 12-degree limit.
- **Shooting range, a person who stops just inside it:** that come-back only came while the aim was
  pinned at the limit. A person who stopped 0.1-0.7 degrees inside it left the camera, which had coasted
  past the limit at walking speed, resting out there for as long as they stood. A camera that is itself
  outside the limits now comes back inside whatever the aim, on the error alone (the walk's
  feed-forward drove it out again: 3-5 reversals at the limit in a first try). motion_bench rig, 24
  degrees, limit 12, the person stopping 0.03-0.25 m inside at 15 m, 4 seeds, P400 and G2: at rest past
  the edge 0-56 % of the run (longest 12.0 s, the whole stand) → 0-0.1 %; pan reversals the same in 27
  of 28 runs (one more in the other: its come-back), pan starts 8-28 → 8-11 a minute. The simulator's
  range scenario: rightmost edge 12.00-12.47 and at rest past it 0-15 % of the samples (2 runs) →
  12.03-12.11 and 0 %.
- **Display Size or Auto Zoom zooming out at a shooting range edge** with the person beyond it: every
  zoom tick widens the picture and moves the allowed pan inward, the move back inside held the zoom
  back, and the zoom and the pan took turns (one-tick zoom pulses, a step-1 pan start after each). A
  move back inside within the dead zone no longer holds the zoom: it runs in one go and the pan follows
  the limit. The person 3 m beyond the right edge at 15 m, 24 → 33 degrees, 4 seeds: P400 7-14 zoom
  starts over 5.8-9.1 s with 5-11 pan starts → 4 over 1.6-1.7 s with none; on air (slow zoom) 12-17
  over 8.5-10.3 s → 1 over 2.1-2.4 s; G2 4-8 → 4-6 over 1.9-2.0 s (on air 9-11 → 1), no pan starts.
  The cost: during the zoom the picture's edge goes up to 1.9 degrees past it (on air and on a G2 up to
  2.2; before, 0.5-2.2 spread over 2-10 s), about a halting area, 3.2-4.2 degree-seconds against
  0.7-2.6; it is back inside when the zoom ends. With the Halting Area off the zoom still stops and
  starts (14-19 times; the error is past its 0.15-degree dead zone, as before), but the pan no longer
  does (9-12 pan starts → 0).
- **Auto Tracking off when it is already off** (the joystick press switched it off: Restart Tracking
  After Manual Operation off) stops a joystick move that is still slowing down at once, as documented:
  0.16 s at Smoothness 5 and 0.44 s at 10 → 0.
- **After a pause** (manual operation, a joystick move, a fixed viewing angle area) the low-passed
  person speed starts from 0 again. It started with the speed from before the pause and drove the first
  commands away from the person (8 degrees: 5 ticks away, error 12.4 → 13.2 % of the width; at
  Smoothness 10, 10 ticks). Now the first step goes towards them.
- **A joystick press in the direction tracking is panning** ramps on from the speed under way instead of
  stopping first (pan step 10, 0, 1, 4 ... 12 → 10, 10, 11, 12). Other axes and reversals stop at once
  as before.
- **Corrected in the docs:** `--check` breaks 31 limits at 0d953f9, not 21; Smoothness 1 is not the
  motion as it was (only the speed low-pass, the step hysteresis and the joystick ramp go; the rest of
  this work applies at every level).

**The bench, before and now** (`python tools/motion_bench.py`, 3 seeds each; pan unless it says tilt).
Each cell: starts a minute / step changes a second / acceleration (% of the picture per s²) / framing
error (% RMS). The bench starts each run from the same clock, so a few numbers differ a little from
those above.

| Case | 0d953f9 | Smoothness 1 | 5 (default) | 10 |
|---|---|---|---|---|
| P400 50°, walk 1.2 m/s | 29.9 / 7.30 / 33.4 / 3.25 | 0 / 3.79 / 11.0 / 0.60 | 0 / 1.42 / 8.6 / 0.55 | 0 / 0.94 / 7.3 / 0.70 |
| P400 8°, walk 1.2 m/s | 21.8 / 4.46 / 125 / 4.24 | 0 / 2.30 / 61.8 / 3.54 | 0 / 1.11 / 47.9 / 3.49 | 0 / 0.67 / 36.6 / 4.29 |
| P400 8°, walk 0.5 m/s | 41.1 / 4.89 / 94.1 / 3.37 | 0 / 3.64 / 39.2 / 1.67 | 0 / 1.17 / 25.2 / 1.79 | 0 / 0.64 / 18.7 / 2.02 |
| G2 50°, walk 1.2 m/s | 31.0 / 9.18 / 34.1 / 3.20 | 0 / 6.90 / 9.9 / 0.63 | 0 / 3.62 / 8.0 / 0.60 | 0 / 2.26 / 7.0 / 0.76 |
| G2 8°, walk 0.5 m/s | 43.9 / 10.14 / 99.3 / 3.59 | 11.2 / 5.73 / 37.1 / 2.38 | 11.2 / 3.10 / 28.3 / 2.33 | 0.9 / 2.26 / 20.0 / 2.18 |
| P400 50° on air, walk 0.5 m/s | 16.8 / 4.91 / 16.0 / 5.42 | 6.5 / 5.93 / 10.6 / 2.80 | 5.6 / 2.62 / 9.2 / 2.83 | 9.3 / 2.18 / 8.4 / 3.19 |
| P400 8° on air, walk 1.2 m/s | lost (233 %) | 0 / 2.28 / 60.9 / 3.61 | 0 / 1.09 / 46.7 / 3.58 | 0 / 0.80 / 41.0 / 5.17 |
| G2 8° on air, walk 1.2 m/s | lost (240 %) | 0 / 5.56 / 54.3 / 4.02 | 0 / 2.68 / 42.4 / 3.71 | 0 / 1.57 / 33.9 / 6.56 |
| P400 8°, stop-and-go | 31.1 / 4.95 / 111 / 6.35 | 11.8 / 4.22 / 84.6 / 7.17 | 12.6 / 3.54 / 72.1 / 8.30 | 11.1 / 3.78 / 64.6 / 11.39 |
| P400 8°, Halting Area off, walk 0.5 m/s, tilt | 206.5 / 7.29 / 75.3 / 1.81 | 6.5 / 0.22 / 11.4 / 1.48 | 7.5 / 0.28 / 11.2 / 1.65 | 5.6 / 0.19 / 7.6 / 1.48 |
| G2 50°, Halting Area off, walk 1.2 m/s, tilt | 119.5 / 3.98 / 24.4 / 0.38 | 9.2 / 0.31 / 6.5 / 0.30 | 6.9 / 0.23 / 4.7 / 0.29 | 6.9 / 0.23 / 4.4 / 0.25 |

(The stop-and-go person starts 11 times a minute.) `--check`: 31 limits break at 0d953f9 (21
smoothness and far-off limits, 10 framing-reference ones), none at
Smoothness 1 and 5, three at 10 (the tight on-air walks: p95 9.0 % P400 and 14.7 % G2, the G2's worst
moment 31 % off). The G2's slow tight walk may make 15 starts a minute instead of 10: over 10 seeds it
makes 9.3 on average, 0-19.6 per seed, so a limit of 10 failed or passed with the seeds.

**The cost, and what is left**
- **Stop-and-go in tight shots frames worse:** when a person stops, the camera overshoots and comes
  back (8 seeds, 8 degrees: P400 6.44 → 8.36 % RMS, overshoot per stop 13.1 → 18.5 % of the width; G2
  8.94 → 10.53 %, overshoot 18.7 → 23.5 %, worst moment 22.7 → 25.4 %, 28.9 % at 10). Past the dead zone
  it has to come back; the camera follows the walk closely now, so it is further along when they stop.
- **A low Tracking Sensitivity in a tight shot:** the smoothing adds to its lag. Sensitivity 3, 8
  degrees, 1.2 m/s, 8 seeds, p95: P400 7.0 % before, 7.1 at Smoothness 1, 10.0 at 5, 18.6 at 10; G2
  10.2 before, 13.3 at 5, 22.9 at 10. At the default sensitivity (7) it frames better than before.
- **G2 with the Halting Area off, a slow drift at 8 degrees:** the 0.46-degree minimum dead zone costs
  framing: 1.95 → 3.28 % RMS, p95 3.74 → 6.09 %, against 122 → 44 starts and 40.5 → 0.4 reversals a
  minute.
- **Walking towards the camera at 8 degrees:** the worst moment is higher (G2 10.3 → 13.1 %, P400 8.2 →
  10.1 %) with a few more pan reversals (G2 2.8 → 4.3 a minute, P400 1.1 → 3.6; P400 starts 38 → 14);
  the RMS is the same.
- **Smoothness 1 makes short stops on a G2 in some wide shots:** without the low-pass and the step
  hysteresis the G2 drops to its step 0 between its slowest steps (slow walk at 50 degrees on air: 16.8
  → 25 starts a minute, standing still 60 → 7 % of the time; at 60 degrees 22 → 31, 58 → 2 %). At 5 it
  makes fewer starts than before.
- **P400 with the Halting Area off, 1.2 m/s at 8 degrees:** the tilt still follows the head bob (207
  starts and 206 reversals a minute, against 125 starts and 208 reversals before; step changes 11.9 →
  7.7 a second). It
  would need a bigger dead zone; a slower walking filter (0.3 and 0.5 s) did not help.
- **P400 at 5 degrees, 1.2 m/s, Halting Area on:** about 47 small tilt corrections a minute (81 % of
  the time still; before, this walker was lost in pan, so there is nothing to compare). A
  0.25-degree minimum dead zone cut them to 16 and the reversals 12.6 → 0 (framing 3.28 → 3.39 %), but
  it changes every tight and halting-off shot: left for a decision. Smoothness 10 cuts the reversals to
  2.3 a minute.
- **Measured and not kept:** zoom-scaled acceleration (on top of the on-air share it lost a 1.2 m/s
  walker at 5 degrees, 146-437 %, and even with a guard that kept the person framed it smoothed nothing
  in stop-and-go: acceleration 66 → 70); relaxing the smoothing as the person drifts off (it trimmed the
  worst moment at 10 on air at 5 degrees only from 33.9 to 25.6 %); a hysteresis on the face keypoints'
  confidence (mixed: the P400's slow walk at 8 degrees framed worse, 1.79 → 2.06 %, the G2's better).
- **The S-curve** promised in research.md 3.4 and plan.md exists now (`Motion.jerk_time`), but only
  above Smoothness 5.
- **Not checked on the cameras:** run `visca_probe.py HOST ramp`, the pan sweep at full tele with
  Adaptive P/T (G2: SpeedByZoom) on and off, and a FreeD log of a walking speaker on a P400.
- **Tests:** 59 new (56 engine, 3 tools); 466 passed and 4 skipped (environmental). ruff, svelte-check
  and the UI build are clean.

## 2026-11-06: layout review of the web UI

Every page was checked in the browser with the simulator at 1920x1080, 1366x768, 1024x768 and a
390-px phone. Changes, each for a problem seen in the screenshots:
- **On/off switches** said "On" next to a switch that was off. They now say On or Off.
- **The mouse mode and View Initial Position** were in the right panel, away from the picture they act
  on. They are now above the pictures, where Canon's app has them (reference.md 4.1).
- **Tracking panel order:** the controls used during a service come first (Auto Tracking, Status,
  target), then On Air and Audio follow, then App Settings 1-5 and the setting sections. **Save** and
  **Initialization** stay at the bottom of the panel instead of below a long scroll. Registered Face
  Tracking can be switched there too (Canon has it on that screen).
- **Composition** is split into Framing (where the person sits, display size) and Movement (halting
  area, sensitivity, smoothness), on the panel and in wizard step 5.
- **Each picture's header** has an Auto Tracking switch instead of an "Auto Tracking OFF" button that
  read like a state. The status badge shows only when tracking is on (it said "Off" twice before).
- **One camera:** the camera list on the left is hidden (it only repeated the picture), and the
  picture uses the freed width but stays sized to fit the screen with its header and info rows.
- **Settings:** fields are at most 1100 px wide on large screens. Pages without an Apply button say how
  their changes take effect. Help texts named a "Settings → Connections" page that is called
  "vMix & mixer". The performance badge in the top bar opens Settings → Performance.

## 2026-11-06: grouped Setting Wizard, updates from GitHub

**Setting Wizard.** The ten pages are no longer only in Canon's order:
- Four groups in the list: Picture area (Pan/Tilt Operation Control, Initial Position), Choosing the
  target (Auto Select, Exclusion Areas, Halting Areas), Framing and movement (Composition, Priority
  Display / Fixed Viewing Angle Areas, Auto Framing), Extras (Registered Faces, Camera Preset Link).
- Main steps (1, 2, 3, 6 in the new order) and optional ones: **Next** goes through the main steps
  only, unless "Optional steps too" is ticked (kept per browser). The list always shows all ten.
- A mark per page: ✓ in use (a range, an area, Auto Select, Auto Framing, faces, a preset link), ! for
  a main step not done (no initial position captured).
- Texts that named a page by its number ("wizard step 4") now name it.

**Updates from GitHub** (`core/updates.py`, Settings → System → Updates; user-guide section 1):
- `.github/workflows/release.yml` builds the setup program on `windows-latest` for every push to
  `main` that changes the program (docs-only pushes don't): models exported once and cached, the
  tests run, `packaging/build.ps1 -Version 0.1.0.<n>`, and a release `build-<n>` with the setup
  program and its `.sha256`. The build number goes into the program as `trackxen/_build.py`.
- The app asks GitHub's API for the latest release with a fine-grained read-only token (the
  repository is private), kept in `update.json` like the PIN in `access.json`, never in an answer
  or the log. It downloads the setup program, checks its SHA-256 against the release's file, runs it
  silently with `/update=1`, and closes itself the normal way (cameras stopped, worker ended); the
  setup program closes what still runs, installs, and starts TrackXen again (`IsUpdate` in
  `trackxen.iss`).
- Refused while a camera is on air, and in a development copy (it can check and download there).
- Tests: `tests/test_updates.py` (token kept apart, release parsing and build comparison, failures
  explained, checksum mismatch rejected, installed-only, on-air refusal, launch then close).
- Not tried here: an install on Windows (this machine is Linux).
- **The first release build on GitHub ran the tests on Windows for the first time** and three failed:
  - A real Windows defect: `time.monotonic()` moves in 15.6 ms steps there, so a FreeD position that
    arrived in the same step as a VISCA position inquiry started didn't count as newer, and the older
    polled position was added after it. Now FreeD in the same step counts as newer
    (`test_freed_in_the_same_clock_tick_as_a_poll_is_still_the_newer` reproduces the coarse clock).
  - `tools/motion_bench.py` gave NaN accelerations on Windows: asyncio runs every timer due within the
    clock's resolution at once, so the 200 Hz samples got equal times. The bench's virtual loop now has
    an exact resolution (same numbers as on Linux).
  - The version test failed once the build number was written into the program; the tests now run
    before that step, and the test accepts a release build's number.

## Audits

- **End of Phase 2:** structure OK. Fixed: camera loops that could die silently; missing Origin check;
  crashes not reaching the log; session logic without tests (8 tests added).
- **End of Phase 4a:** added ruff (lint clean). Fixed: preset numbers not range-checked; area on/off
  values not coerced to booleans; a failed absolute move left a stale "moving" state; an effect in
  the wizard that reset the mouse mode ten times a second. Structure: Canon's rules live in
  `rules/` (pure functions, unit-tested), the session only sequences them.
- **End of the night (Phases 4b-7):** `core/app.py` had grown to 651 lines with three jobs; registered
  faces moved to `FaceManager` (`core/faces.py`) and vMix tally / mixer / audio-follow to `LiveInputs`
  (`core/live_inputs.py`); app.py is 489 lines. Fixed: the status said "No target selected" while
  Registered Face Tracking was looking for faces (now "Searching"); an outdated docstring. Checked:
  every POST route and the WebSocket keep the Origin check; log and face-thumbnail paths can't be
  steered outside their folders; a windowed program without a console doesn't crash on prints.
  121 unit tests, ruff clean, svelte-check clean; all 10 simulator scenarios pass after the refactor.

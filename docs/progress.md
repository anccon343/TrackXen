# Progress log

The app is called **TrackXen** (code package `trackxen`). Open questions are in `docs/questions.md`.

## How to run it (development)

```
.venv\Scripts\python tools\sim_camera.py        (window 1: a virtual P400 with NDI video)
.venv\Scripts\python -m trackxen --sim          (window 2: TrackXen)
open http://127.0.0.1:8090                       (the UI; /test is the simple Phase 2 page)
.venv\Scripts\python tools\sim_test.py          (optional: automatic 30 s tracking test)
.venv\Scripts\python tools\sim_scenarios.py     (optional: Canon rules end to end, 8 scenarios, ~6 min)
.venv\Scripts\python tools\get_face_models.py   (once per PC: the face models for Registered Face Tracking)
.venv\Scripts\python tools\hardware_test.py     (measure the PC, recommend Light / Auto / HYPER)
.venv\Scripts\python -m pytest                  (109 tests)
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

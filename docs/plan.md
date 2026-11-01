# Build plan: BirdDog P400 speaker tracker

**Status:** approved 2026-10-31 ("install python and start building"). By the morning of 2026-11-01
Phases 0 and 2-7 are built and checked in the simulator (details and measurements in `progress.md`);
still open: Phase 1's measurements on the real P400s, a service-length soak test on the real
cameras, compiling the setup program (needs Inno Setup, `questions.md` 7) and Phase 8 (optional).

**Background:**
- `reference.md`: what Canon's app does, and descriptions of your two UI screenshots.
- `research.md`: why each technology was chosen.

---

## 1. What v1 is

**In scope**
- **Canon's feature set:** the Canon Auto Tracking App (RA-AT001) rebuilt for **4 × BirdDog P400**,
  each camera tracking its own person. Every feature in `reference.md` sections 2–4 is included:
  - the 8 area types and their rules
  - the 10-step setup wizard, also usable as individual pages
  - 5 named App Settings per camera
  - Auto Framing: Multi-Person, Face Direction, Sit/Stand
  - registered faces (7 slots)
  - camera preset link
  - the Tracking Execution screen and status badges
- **Your two extras:**
  - **On-air motion:** smoother, slower moves while a camera is live on Program (vMix tally).
  - **Audio-follow:** the active microphone on your **Midas MR18** (or X Air) picks the target.
- **Installable app** (requirement added 2026-10-31): downloaded and installed on **any Windows 10/11 PC**,
  not tied to this machine. It runs best on an NVIDIA RTX card, and also runs on other GPUs or CPU only,
  with fewer cameras or lower rates. Tablets and phones install nothing; they open the app in a browser.
- **Web UI** in Canon's style, used from the PC and from tablets/phones.
- **Performance modes** (requirement added 2026-10-31), switchable at any time:
  - **Light**, for slower PCs or when sharing with vMix: smallest model, lower input resolution, fewer
    detections per second, NDI low-bandwidth stream, face checks less often.
  - **Auto** (default): picks settings that fit the PC, from a benchmark on first run.
  - **HYPER**: the best and most responsive tracking, whatever the load.
    - The largest models and the highest input resolution.
    - Detection on every frame (up to 60 per second), full-bandwidth NDI.
    - Faces and body re-ID on every frame, and the fastest control loop.
    - The statistics page still warns when the PC can't keep up; the safety watchdog always stays on.
- **Statistics and problem indicators** (requirement added 2026-10-31):
  - Basic PC load (GPU, CPU) is always visible in the UI.
  - Problem lights show on screen: a camera, NDI, FreeD, the mixer or vMix not responding, the frame
    rate dropping, or the GPU overloaded.
  - A **Statistics button** (like vMix's) opens the statistics page with the advanced details per
    camera and for the PC.
  - A dedicated **text log file** (`TrackXen-YYYYMMDD.log`) is written automatically: every
    event plus a one-line statistics summary every 10 seconds, readable in Notepad. Machine-readable
    copies (`.jsonl`) feed the graphs and the log viewer.
- **Connections:** video over **NDI**; control over **VISCA over IP**, with **FreeD** for live position
  and the **BirdDog REST API** for camera setup.

**Not in v1** (later or not wanted)
- Automatic lower thirds: not wanted.
- HDMI/SDI capture-card input: Phase 7.
- Automatic cutting in vMix, virtual PTZ crop output, camera-to-camera hand-off, Q&A raised-hand mode,
  and lip-movement speaker detection: ideas for later.

## 2. Decisions

| Area | Choice |
|---|---|
| Engine | Python 3.12 in its own virtual environment |
| Finding people | **Ultralytics YOLO26-pose**: one model gives each person's box plus body keypoints (enough for face direction, sit/stand, raised hands). Exported to **ONNX** and run with **ONNX Runtime**, which picks the best engine on each PC: TensorRT FP16 on NVIDIA, DirectML on AMD/Intel, CPU as a last resort. Detection-only YOLO26 and RF-DETR as fallbacks |
| Following people | ByteTrack / BoT-SORT-style trackers in plain Python/numpy (Roboflow `trackers`, or our own), one per camera, fed with camera movement from FreeD. No PyTorch needed in the installed app |
| Faces | YuNet (find) + SFace (recognise), from OpenCV |
| Identity lock | Face when visible, plus a body re-ID model (ONNX), added in Phase 4c |
| Model tooling | Ultralytics (with PyTorch) runs only on the development PC, to export and fine-tune models. The installed app just runs the exported ONNX files |
| Packaging | PyInstaller bundle + Inno Setup installer (one `.exe`), start-menu shortcut, tray icon, uninstaller; update check against GitHub Releases |
| First run on a new PC | Hardware check, then an automatic performance tier, then a TensorRT engine build for that GPU (a few minutes, once). After that it finds the NDI sources, P400s and MR18 on the network |
| NDI runtime | Bundled under NDI's SDK licence (with the required NDI attribution), or installed from NDI's site |
| Performance modes | Light / Auto / HYPER. Models are exported in several sizes (YOLO26 n/s/m/l-pose) and input resolutions (480–1280); the mode chooses which one runs and how often |
| Statistics | `psutil` (CPU, memory, network) and NVIDIA's NVML (GPU load, video memory, temperature), plus the app's own counters. Written once a second, with an event log, to files in the app's data folder (kept for a set number of days) |
| GPU runtime | ONNX Runtime's own CUDA/cuDNN packages (`onnxruntime-gpu[cuda,cudnn]`), which ship with the app; PyTorch's CUDA is only used for development |
| Video in | NDI 6 via `cyndilib` (NDI 6 Tools are already installed on this PC) |
| Camera control | Our own VISCA-over-IP client (UDP 52381), FreeD listener, BirdDog REST client (port 8080) |
| vMix | TCP API client (port 8099), `SUBSCRIBE TALLY` |
| Mixer | OSC client for MR18/X Air (UDP 10024), same method as Companion's module |
| UI | **Svelte 5 + TypeScript + Vite**, served by the engine (FastAPI + WebSocket). Open it in a browser; tablets use the PC's address. (React works equally well if you prefer it) |
| Settings | JSON files checked against a schema; the same format doubles as "show files" for saving and loading a whole setup |
| Code | Local git repository in `Desktop\birddog-tracker`; I commit only when you say so |

## 3. How it fits together

```
 P400 x4 ──NDI video───────────────▶ VISION WORKER (process)
   ▲  │                               YOLO26-pose (all cameras in one batch),
   │  │                               trackers, faces, preview images
   │  │                                        │ detections + previews
   │  └──FreeD position (UDP)──────▶ CORE SERVICE (process) ◀── vMix tally (TCP 8099)
   │                                  control loop per camera   ◀── MR18 levels (OSC 10024)
   └──VISCA-over-IP (UDP 52381)◀───── rules, settings, watchdog
                                               │ WebSocket
                                      WEB UI (PC browser, tablets, phones)
```

- **Core service:** the part that must always respond. It holds:
  - the web server and settings
  - camera connections
  - a 50 Hz control loop per camera
  - the Canon rules (areas, auto select, composition)
  - the vMix and mixer connections
  - a watchdog
- **Vision worker:** the heavy part. NDI video in, AI models, trackers, faces, preview images for the UI.
- **Why two processes:** if the heavy part stalls or crashes, the core **stops all cameras** and
  restarts it. A hiccup in vision never leaves a camera moving.
- **Launcher:** a small tray app starts both processes and opens the UI. Its menu has "Open",
  "Stop all cameras" and "Quit".

**Control loop, per camera:**
1. **Find the target in room angles:** its position in the frame, plus the camera's FreeD position at
   the moment that frame was captured.
2. **Predict:** a Kalman filter predicts where the target will be when the next command takes effect,
   covering the video and command delays.
3. **Decide the camera angle and zoom:** apply Display Position and Size, the dead zone, the shooting
   range, and priority/fixed areas.
4. **Turn it into a speed command:** the target's own speed plus a correction, limited by the active
   motion profile (normal or on-air), smoothed (S-curve), and mapped onto the P400's measured speed steps.
5. **Watchdog:** no fresh data means **stop**.

**Automatic calibration** (once per camera, stored):
- field of view at each zoom position
- the real speed of each VISCA speed step
- the video delay and the command delay

## 4. Phases

Each phase ends with something you can try, and a check-in with you before the next phase starts.

### Phase 0: Preparation (small)
**You:**
- Update all four P400s to the latest firmware (5.5.115 / MCU 035).
- Give the cameras, the MR18 and the tracking PC fixed IP addresses.
- In BirdUI → CamControl → FreeD on each camera: enable FreeD and send it to the tracking PC, using
  ports 40001–40004 (one per camera).
- Send me: camera IPs and NDI names, the MR18's IP, the vMix PC's IP.

**Me** (with your OK for the installs):
- Install Python 3.12 (winget) and create the project, its Python environment and the local git repository.
- Install the packages, and check that YOLO26 runs on the RTX 4080 SUPER, both through Ultralytics and
  as an ONNX file through ONNX Runtime (TensorRT, DirectML and CPU).

**Done when:** an environment-check script passes.

### Phase 1: Camera lab, measure before designing (medium)
Small command-line tools, no UI yet:
- **NDI probe:** receives each camera; reports frame rate, resolution and delay. Compares full vs
  low-bandwidth NDI.
- **VISCA probe:** moves and stops; runs every speed step; checks that stops always work.
- **FreeD probe:** decodes the position data; checks rate and units; compares with the VISCA position inquiry.
- **Calibration v0:** speed table, field of view vs zoom, delays.
- **Slow-speed test:** smoothness at the slowest speeds, VISCA vs NDI PTZ. This decides how calm
  on-air moves can be.
- **MR18 meters:** live levels and channel names.
- **vMix tally:** live Program/Preview states.
- **Recorder:** saves NDI video + FreeD to disk, so we can test later without the cameras.

**Output:** `docs/lab-report.md` with the measured numbers.
**Decide together:** which channel moves the cameras (VISCA or NDI PTZ), full or low-bandwidth NDI,
and the slowest usable on-air speed.

### Phase 2: One camera follows one person (large)
- Core service + vision worker; YOLO26-pose on TensorRT; tracker.
- Click-to-track on a simple test page.
- Control loop with dead zone and framing position; auto zoom.
- Lost-target behaviour: hold the shot, or return home after N seconds.
- Watchdog and emergency stop.
- **Statistics from day one:** a collector that records counters every second (frame rates, delays,
  model time, tracking state, VISCA/FreeD traffic and errors, CPU/GPU load) plus events (target
  locked or lost, camera stopped, connection lost) to the statistics log file.
- **Replay mode:** run everything on recorded clips, plus a simple simulated camera, to tune without hardware.
- **Portable from day one:** models run through ONNX Runtime with automatic engine choice.
- **Installer smoke test:** package the Phase 2 app and install it on a second PC with no dev tools,
  to catch packaging problems early.

**Done when:** a camera follows a person walking across the stage smoothly and keeps them framed. It
never runs away, and it stops if the app or the network drops. The packaged version runs on a second PC.

### Phase 3: Canon-style UI (large)
- **Main screen** (like Canon's Remote Camera Control App):
  - camera list
  - 2×2 live grid with overlays, tally colours and status badges
  - right panel with PTZ pad, zoom/focus and presets
- **Tracking Execution screen per camera:**
  - settings column and App Settings 1–5
  - Auto Tracking switch and status badge
  - mouse modes: Click Centering, Tracking Target Select, Silhouette, Set Area
  - View Initial Position
- **Settings pages** in Canon's web-settings style: icon rail, navigation column, green Apply / grey
  Clear, and a marker on changed fields.
- **Look and feel:** dark theme and colours from `reference.md`; big enough touch targets for tablets.
- **Status bar and problem lights:**
  - A **Statistics** button in the bottom bar, like vMix, opens the statistics page.
  - GPU and CPU load are always visible.
  - Green/yellow/red lights for each camera and for the mixer, vMix and the PC.
  - Click a light to see what's wrong.

**Done when:** everything from Phase 2 works from the PC and from a tablet.

### Phase 4: Canon feature parity (large, four parts)
- **4a Areas and composition:**
  - shooting range and fixed tilt
  - initial position and lost-target behaviour
  - priority display area and fixed viewing-angle areas
  - auto select, with start time and area
  - restart after manual operation
  - Display Position and Size, auto zoom, pan/tilt halting area, tracking sensitivity
  - exclusion areas and tracking-halting areas
  - area rules and error messages
  - Setting Wizard (1–10, "n/10" footer) plus individual pages
  - 5 named App Settings; Save and Initialization
- **4b Auto Framing:** Multi-Person, Face Direction, Sit/Stand.
- **4c Registered faces:**
  - 7 slots; registration with the green capture frame
  - priority by drag; on/off; delete
  - consent reminder
  - identity lock with face + body re-ID
- **4d Camera Preset Link:**
  - preset table (On / Off / blank + App Settings slot)
  - recall presets from the UI
  - notice recalls made elsewhere, where FreeD allows

**Done when:** every item in `reference.md` section 3 works on the real cameras.

### Phase 5: Your two extras (medium)
- **5a On-air motion:**
  - vMix tally connection
  - camera ↔ vMix input mapping, suggested from NDI names
  - an on-air motion profile per App Settings: lower speed, soft starts and stops, bigger dead zone,
    slow or no zoom, no big jumps while live
  - smooth blending between profiles
  - a manual "on air" switch as fallback
- **5b Audio-follow:**
  - MR18/X Air connection: levels, channel names, mutes
  - mapping screen: mic → person / preset / area, plus which cameras may respond
  - speech detection: threshold, ~0.3 s attack, ~2 s hold, priority list
  - an off-air camera is preferred for picking up a new speaker
  - manual override always wins

**Done when:** in a rehearsal, speaking into a mic sends an off-air camera to that person, while the
live camera only makes slow, small corrections.

### Phase 6: All four cameras, robustness (medium)
- All four cameras at once.
- Hardware auto-detection, plus the **Light / Auto / HYPER** performance modes. Each mode sets the model
  size, input resolution, detections per second, NDI bandwidth, how often faces and re-ID run, and the
  control-loop rate. The first-run benchmark picks the Auto settings.
- **Statistics page (advanced):**
  - **Per camera:** frame rate and dropped frames, video delay, model time, tracking state and target
    changes, VISCA commands and errors, FreeD rate, on-air time.
  - **PC:** CPU, GPU, video memory, GPU temperature, network traffic.
  - **Connections:** mixer and vMix status.
  - Live graphs.
- **Statistics log viewer:** open a past session, see the timeline of events and graphs, and export it.
- Auto-reconnect and logs.
- Show files: save and load a whole setup.
- Soak test.

**Done when:** a full service-length run with four cameras has no crash, and vMix on the same PC isn't disturbed.

### Phase 7: Installer and distribution (medium)
- **Windows installer:** one `.exe` with start-menu shortcut, tray icon and uninstaller.
- **First-run wizard on a new PC:**
  - hardware check, then the performance tier
  - TensorRT engine build with a progress bar (other engines are used while it builds)
  - network discovery: NDI sources, P400s (BirdDog REST), MR18 scan
  - Windows firewall rules for the UDP ports
  - a FreeD check: if a camera's FreeD goes to another PC, the app shows how to point it here, and
    meanwhile polls the camera's position over VISCA
- **Moving between PCs:** settings stored per user (`%APPDATA%`); show files carry a whole setup from
  one PC to another.
- **Updates:** update check against GitHub Releases.
- **Licence notices:**
  - The app is AGPL because of the Ultralytics models.
  - NDI attribution.
  - Third-party licences.
  - If you ever give the app to people outside your church, the source has to be shared too, for
    example as a public GitHub repo.
- **Download size:** the large NVIDIA runtime files (roughly 1–2 GB, check) are downloaded on first run
  on NVIDIA PCs, so the installer stays small.

**Done when:** a clean Windows PC with no dev tools installs the app from one file and tracks a
camera after the first-run wizard.

### Phase 8: Later / optional
- HDMI/SDI capture-card input (Magewell / DeckLink).
- Companion / Stream Deck control over HTTP/OSC.
- Fine-tuning the model on your church footage.
- More nice-to-haves from `research.md` section 5.
- Finnish UI translation.

## 5. Project layout

```
birddog-tracker/
  docs/            reference, research, plan, lab report
  engine/trackxen/  Python package
    core/          app, API/WebSocket server, settings, watchdog, statistics and logs
    devices/       VISCA, FreeD, BirdDog REST, NDI input
    integrations/  vMix, MR18/X Air
    vision/        worker, models, trackers, faces
    control/       controller, Kalman filter, motion profiles, calibration
    rules/         areas, target selection, composition, auto framing
  ui/              Svelte app
  tools/           lab probes, recorder, model export scripts
  packaging/       PyInstaller spec, Inno Setup script, first-run assets
  tests/           unit tests and replay tests
  models/, data/   not in git: model files, settings, face data
```

## 6. Testing
- **Unit tests** for every protocol (VISCA packets, FreeD, MR18 meters, vMix tally) and for the area rules.
- **Replay tests:** recorded clips + FreeD data, so decisions can be checked without cameras.
- **Simulated camera:** a simple one for tuning the control loop.
- **Real-camera checklist** at the end of each phase, and a full rehearsal before the first live service.

## 7. Risks

| Risk | Effect | What we do |
|---|---|---|
| The P400's slowest speed step is too fast or coarse | On-air moves look steppy | Measure in Phase 1; try tiny absolute moves or NDI PTZ; widen the on-air dead zone |
| FreeD is missing, slow or in odd units | No exact camera position | Fall back to VISCA position inquiries at 10–20 Hz |
| NDI delay is high | Late moves, overshoot | Predict using the measured delay; P400 Low Latency mode where SDI/HDMI isn't needed; low-bandwidth stream |
| vMix and the tracker share a PC | GPU/network competition | Light mode, low-bandwidth NDI, health panel |
| Faces are hard to recognise (light, angle) | Wrong person | Body re-ID + operator override; faces stay optional |
| The scope is big | Takes a long time | Every phase is usable on its own; Phase 4 can be trimmed after Phase 3 |
| Python is too slow somewhere | Stutter | Profile, and move only that piece to native code |
| The installer gets huge (NVIDIA runtime files) | Slow download | Small base installer; NVIDIA files downloaded on first run |
| First start on a new PC is slow (TensorRT build) | Minutes of waiting | Progress screen; other engines meanwhile; cached afterwards |
| Weaker PCs (no NVIDIA GPU) | Fewer cameras or lower rate | Performance tier chosen automatically; the health panel shows the limit |
| FreeD can only send to one PC | A second PC gets no FreeD | Fall back to VISCA position polling; the app explains how to point FreeD at this PC |
| Installer isn't code-signed | Windows SmartScreen warning | Explain it in the install notes; code-signing certificate optional later |

## 8. What I need from you to start
1. Your sign-off on this plan, or the changes you want.
2. The Phase 0 tasks (firmware, fixed IPs, FreeD on, the addresses).
3. During development, is **this PC** (RTX 4080 SUPER) on the same network as the cameras, mixer and
   vMix? If not, you run the lab tools at the church and send me the results.
4. OK for me to install Python 3.12 with winget and set up the project's environment.
5. Later (Phase 2 smoke test and Phase 7): a second Windows PC to test the installer on.

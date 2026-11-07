# Research: technology for the BirdDog P400 tracker

Written 2026-10-31.

**Goal:** automatic speaker tracking (see `../features.md`) for 4 × BirdDog P400 on one Windows PC
with an NVIDIA GPU, working alongside vMix.

This is research, not a plan: the build plan (plan.md) made the decisions. Numbers marked "check" still need
to be confirmed on the real cameras or in the source.

---

## Requirements (2026-10-31) and what they change

| Topic | Requirement | What it changes |
|---|---|---|
| GPU / shared PC | RTX 40-series or newer; the tracker sometimes runs on the vMix PC | Plenty of GPU for 4 cameras. When sharing with vMix the app needs a "light" mode, and should pull low-bandwidth or capture-card video so the PC doesn't receive every camera twice (section 6) |
| Video path | Mostly NDI, but HDMI/SDI capture must also work, with control still over IP | Video source becomes a per-camera choice (NDI or capture card); movement and position always go over IP (VISCA + FreeD). See 1.6 |
| In-house or sold | Personal, non-commercial project for a church (clarified later the same day) | Ultralytics (YOLO26, AGPL-3.0) is fine to use, which simplifies the stack (section 2). If the app is ever shared, it must be shared as open source under AGPL. InsightFace's free face models are for non-commercial *research*; church use isn't research, so that's still a grey area and SFace stays the default |
| Tablet / phone control | Yes | Confirms the browser-based UI (option A in 3.1) |
| Distance and lighting | Different every time | Setup must be quick per venue (wizard). Detection must cope with small, far-away people (higher input resolution or tiling when zoomed out) and changing light; fine-tune on varied footage |
| Mics | Not separate in vMix; levels can come from the Behringer mixer, where mics can be assigned to presets/persons | Audio-follow reads mic levels straight from the mixer over the network. See 2.7 |
| Lower thirds | Not needed | Removed from the nice-to-haves |
| Mixer (2nd round) | Mainly a Midas MR18, sometimes a Behringer X Air | Both use the same X Air OSC protocol (UDP 10024), so one connector covers both, and the app can listen to more than one mixer. See 2.7 |
| Video (2nd round) | Mostly NDI | NDI is the main path; capture-card input stays possible but is lower priority |
| Extras chosen (2nd round) | Smoother, slower moves while live on Program; choose the target from the active mic | These two are in scope (3.4 and 2.7). The other nice-to-haves stay ideas for later |

---

## 0. Summary: recommendations

| Topic | Recommendation | Main reason |
|---|---|---|
| Video in | NDI (NDI SDK 6) by default, or an HDMI/SDI capture card, chosen per camera; detect on a downscaled frame (~960 px wide) | On the network the P400 only sends Full NDI; capture cards cover SDI/HDMI setups. Control stays on IP either way |
| Moving the camera | **VISCA over IP** (UDP port 52381): speed-based pan/tilt/zoom plus absolute moves | Documented, low latency, separate from the video link, the industry standard |
| Knowing where the camera points | **FreeD** output from each P400 to our PC (pan/tilt/zoom/focus/iris every frame); VISCA position inquiry as backup | Needed for areas fixed to the room, for moving-camera tracking, and to spot manual moves and preset recalls |
| Camera setup | BirdDog REST API 2.0 (HTTP, port 8080) | Speed limits, presets 1–9, exposure, encode and tally settings. It has **no movement commands** |
| Person detector | **Ultralytics YOLO26** (AGPL-3.0, fine for this non-commercial project), run as TensorRT FP16; RF-DETR as the alternative to benchmark | Top-tier speed and accuracy; one library covers detection, pose, tracking, fine-tuning and TensorRT export |
| Tracker | Ultralytics' built-in BoT-SORT (optional ReID) or ByteTrack, with camera motion taken from FreeD | Comes with YOLO26; FreeD solves most of the moving-camera problem |
| Keeping the right person | OSNet body re-ID plus face recognition when the face is visible | Re-finds the speaker after people cross or block each other |
| Faces | YuNet (detect, 5 landmarks) + SFace (recognise), both from OpenCV Zoo | Commercially usable. InsightFace's ArcFace is more accurate but needs a paid licence for commercial use |
| Body pose | YOLO26-pose (same library); RTMPose / RTMO as alternatives | Sit/stand, raised hands, body direction |
| Inference | ONNX Runtime GPU with the TensorRT provider (FP16, engine cache); native TensorRT if we need the extra speed | Easiest path on Windows with NVIDIA |
| App structure | **Python engine** (one worker per camera, shared GPU) plus a **browser-based UI** (TypeScript), optionally in its own desktop window | Best ML tools, fastest to tune; a browser-based UI also works on tablets |
| vMix | TCP API (port 8099): tally + activator subscriptions | Tally-aware camera behaviour |
| Mic levels | Straight from the X Air / Midas MR18 mixers over the network (OSC, UDP 10024) | Lets the active mic decide who a camera should be on |
| On-air behaviour | vMix tally switches each camera to a slower, smoother motion profile while it's on Program | Tracking corrections don't show as jerky moves on the live picture |

---

## 1. Talking to the P400

### 1.1 What the P400 offers (BirdDog user guide, release notes, API docs)
- **Lens and head:**
  - Sony 1/2.5" 8.5 MP sensor.
  - 20× optical zoom; Super Resolution Zoom stretches it to 30× at 4K.
  - About 350° pan and 120° tilt, up to 150°/s.
  - 128 presets.
- **Video:** Full NDI only (no NDI|HX), bitrate 60–360 Mbps, sent at the same time as 6G-SDI and HDMI.
- **Control:**
  - NDI PTZ control.
  - VISCA over IP (**UDP 52381**).
  - VISCA or Pelco-D over RS-232/422/485.
- **FreeD output:**
  - Sends pan/tilt/zoom/focus/iris every frame to an IP and port set in BirdUI → CamControl → FreeD tab.
  - Arrived as beta in firmware 5.0; BirdDog lists it for all its cameras.
- **Low Latency video mode:** reduces delay, but switches off SDI/HDMI and the on-screen menu.
  Formats are limited to 2160p up to 30 fps and 1080p up to 60 fps.
- **Adaptive P/T:** the camera slows pan/tilt itself as it zooms in. We'd probably switch this off and
  do our own zoom-based speed scaling (check).
- **Firmware:**
  - BirdDog's knowledge base lists P400 firmware as NDI 5.5.115 / MCU 035.
  - Release 5.5.089 says pan/tilt movement is improved at low speeds, which matters for smooth tracking.
    Update all four cameras.
  - Known limitation: the VISCA address can't be changed on P400/P4K. Irrelevant over IP.

### 1.2 The four ways to talk to the camera

| Channel | Use it for | Good | Limits |
|---|---|---|---|
| **VISCA over IP** | All movement: continuous pan/tilt with speed, zoom speed, absolute pan/tilt/zoom, focus, presets, position inquiries | Standard Sony protocol over UDP, very low latency, independent of video, supported by many tools | Integer speed steps (Sony-style, pan ~1–24, tilt ~1–20); how smooth the slowest steps are must be tested; we handle retries and sequence numbers ourselves |
| **NDI PTZ** (control messages inside the NDI connection) | Backup movement path | Decimal speeds −1…+1, absolute pan/tilt −1…+1, zoom 0…1, no extra network setup | NDI's docs contradict each other on zoom direction; no position read-back; only works while the video connection is up |
| **FreeD** (camera → PC, UDP) | Live position feedback | Every-frame position without polling; the standard for AR/virtual studio tracking | Needs setup per camera; P400 units and scaling to be checked |
| **BirdDog REST API 2.0** (HTTP 8080) | Setup and status | JSON; PTZ max speeds (pan 0–21, tilt 0–18, zoom 0–7), presets 1–9 save/recall, exposure, white balance, picture, encode (bandwidth 60–360, tally mode), reboot, `/about` | No pan/tilt/zoom move commands; only presets 1–9 |

**Recommendation:**
- VISCA-IP for commands, FreeD for position, REST for setup, NDI for video.
- Keep NDI PTZ as a fallback and compare it with VISCA for smoothness at slow speeds.

### 1.3 VISCA-over-IP details
- **Packet layout:** an 8-byte header followed by the normal VISCA message.
  - Header fields: payload type (2 bytes), length (2), sequence number (4).
  - Payload types: 01 00 = command, 01 10 = inquiry, 01 11 = reply, 02 00 = control (reset sequence number).
  - Same format as BirdDog's published X-series pages.
- **Messages we'd use** (Sony standard, matching BirdDog's X4/XL lists):

  | Function | Message |
  |---|---|
  | Pan/tilt drive at a speed | `8x 01 06 01 VV WW XX YY FF` |
  | Absolute pan/tilt at a speed | `8x 01 06 02 VV WW 0p 0p 0p 0p 0t 0t 0t 0t FF` |
  | Pan/tilt position inquiry | `8x 09 06 12 FF` → `90 50` + 4 pan nibbles + 4 tilt nibbles |
  | Zoom at a speed | `8x 01 04 07 2p` (tele) / `3p` (wide) `FF` |
  | Zoom to a position | `8x 01 04 47 0p 0q 0r 0s FF` |
  | Zoom position inquiry | `8x 09 04 47 FF` |
  | Preset recall / set | `8x 01 04 3F 02 pp FF` / `8x 01 04 3F 01 pp FF` |

- BirdDog doesn't publish a P400-specific command list online (it does for the X series), so each
  command is verified on the camera.

### 1.4 FreeD
- **Message format:** a 29-byte "D1" message:
  - byte 0: type (0xD1); byte 1: camera ID
  - pan, tilt and roll angles: 3 bytes each
  - X/Y/Z position
  - zoom and focus: raw lens counts, 3 bytes each
  - 2 spare bytes, then a checksum
- **Units:** angles are fixed-point degrees (1/32768° in the spec). Zoom and focus scaling differs by
  vendor, so we calibrate zoom counts against the real field of view.
- **What it gives us:**
  - Areas stored as room angles.
  - Exact camera position for click-to-centre and for tracking while the camera moves.
  - Knowing when someone else (vMix, a joystick) moved the camera.
  - Knowing when a preset was recalled.

### 1.5 Test on a real P400 before designing anything
1. Slowest smooth pan/tilt speed over VISCA vs NDI. Is speed step 1 slow enough for a 20× close-up?
2. FreeD: how often it sends, how delayed it is, the units, and the zoom count ↔ field-of-view curve.
3. Glass-to-glass delay of NDI to the PC, in normal vs Low Latency mode.
4. What happens when vMix (or a joystick) and our app both send commands.
5. NDI low-bandwidth stream: resolution and delay. Could it be used for detection?
6. Adaptive P/T on vs off.

### 1.6 Video input: NDI or HDMI/SDI capture
The P400 sends NDI, SDI and HDMI **at the same time**, so the tracker and vMix don't have to take video
the same way. Movement (VISCA) and position (FreeD) go over IP in every case.

| Setup | How the tracker gets video | Notes |
|---|---|---|
| vMix on NDI, tracker on NDI | Its own NDI connection to each camera | Simplest. On a shared PC, use the NDI low-bandwidth stream for tracking so the PC doesn't pull every camera twice at full rate |
| vMix on SDI, tracker on another PC | NDI from the camera, or the camera's HDMI into the tracker's capture card | The camera's spare output avoids any sharing problem |
| vMix on SDI, tracker on the same PC | Four ways, listed below | Blackmagic DeckLink inputs normally serve one program at a time, so vMix and the tracker can't share one DeckLink input |

Ways for the tracker to get video when vMix takes SDI on the same PC:
- NDI straight from the camera.
- A Magewell card, which can feed one input to several programs at once.
- The camera's HDMI into a second capture input.
- A vMix NDI output set to that camera (4K/Pro/Max editions have up to 4 NDI outputs).

Capture cards from Python:
- **Blackmagic DeckLink:** use the DeckLink SDK directly; reported delay is 0–1 frame, while going through
  DirectShow reportedly adds 3–30 frames. Python wrappers exist (`blackmagic-io`, `pydecklink`) but are
  young, so a small native module of our own may be more reliable.
- **Magewell Pro Capture:** Windows SDK with a low-latency transfer mode, wrapped by `pymagewell`.
- **USB (UVC) capture devices:** work through Windows Media Foundation / DirectShow (OpenCV, FFmpeg) but
  usually add more delay.

Other things that change with capture input:
- **Camera mapping:** each capture input must be assigned to the right camera in the app. NDI sources
  name themselves; capture inputs don't.
- **Different delay per path:** the app should measure each camera's video delay. It moves the camera
  and compares when FreeD reports the move with when the picture moves, then uses that delay in the
  control loop. Worth doing for NDI too.

### 1.9 PTZOptics G2 (PT20X-SDI-GY-G2), researched 2026-11-01

**Control**
- PTZOptics takes plain VISCA, without the 8-byte Sony header, on UDP 1259 or TCP 5678; both ports can
  be changed in the web page ([PTZOptics: IP control](https://docs.ptzoptics.com/articles/miscellaneous/misc-cameras/an-introduction-to-ip-control-&-scripting-for-ptzoptics-cameras)).
  Some firmware also offers Sony VISCA over IP on 52381 (reported for G3: [firmware changelog](https://ptzoptics.com/firmware-changelog)).
- The camera reportedly accepts one IP control connection at a time
  ([vMix forum](https://forums.vmix.com/posts/m56364-vMix-Cannot-Connect-To-PTZ)). TCP would compete with
  vMix or a joystick, so TrackXen uses UDP.
- **Commands** ([VISCA API: movement](https://docs.ptzoptics.com/dev/visca-api/movement),
  [inquiries](https://docs.ptzoptics.com/dev/visca-api/inquiries)):
  - pan speed 0x01-0x18, tilt speed 0x01-0x14 (the P400 goes to 0x17), zoom speed 0-7
  - absolute/relative position `81 01 06 02/03 VV WW 0Y0Y0Y0Y 0Z0Z0Z0Z FF`
  - zoom position inquiry `81 09 04 47 FF` → `90 50 0p 0q 0r 0s FF`
  - pan/tilt position inquiry `81 09 06 12 FF` ([PT limits guide](https://docs.ptzoptics.com/articles/miscellaneous/misc-cameras/pt-limits-packet-sender))
  - an inquiry answers on socket 0 without an ACK; commands get an ACK and then a completion on
    socket 1 or 2
- **Not published:** the position units per degree and the zoom range. `tools/visca_probe.py calibrate`
  measures them from the end stops: data sheet pan ±170°, tilt -30°..+90°; field of view 60.7° wide to
  3.36° tele; pan 1.7-100°/s, tilt up to ~70°/s
  ([Markertek](https://www.markertek.com/product/ptz-pt20xsdigyg2/ptzoptics-20x-optical-zoom-hd-sdi-hdmi-ip-network-rj45-cvbs-1920-x-1080p-60-7-degree-fov-gray-us-style-power)).
- **No FreeD on G2:** FreeD came with newer models such as the Move 4K ([PTZOptics: FreeD](https://www.ptzoptics.com/freed/)),
  so the position is polled over VISCA (20 Hz).
- **Found in the simulator:** without the IP header's sequence numbers, a command's completion that
  arrives late was taken as the answer to the next position question (73 times in 2 minutes). Fixed by
  matching replies the way VISCA defines them: answers on socket 0 with data; completions on the
  command's socket.

**Video**
- `rtsp://IP:554/1` is the main stream and `/2` the sub stream, with no login by default
  ([spec sheet](https://www.fullcompass.com/common/files/46399-PT20XSDISpecSheet.pdf)). Users report
  noticeable RTSP delay but publish no figures ([PTZOptics forum](https://help.ptzoptics.com/support/discussions/topics/13000022944)),
  so `tools/stream_probe.py --delay` measures it.
- **Decoding:** FFmpeg's low-delay options (no input buffering, `max_delay=0`, low-delay decoder) are
  set through PyAV; OpenCV's environment-variable route for them is reported unreliable
  ([OpenCV forum](https://forum.opencv.org/t/rtsp-ffmpeg-set-flags/14382)). Others brought RTSP delay from
  ~550 ms to ~100 ms with `low_delay` ([FFmpeg users](https://ffmpeg.org/pipermail/ffmpeg-user/2024-November/058823.html)).
  Slice threads only: frame threads hold frames back.
- **The quickest way in:** SDI into vMix, then vMix's NDI output, or an SDI capture card on the
  TrackXen PC. Both are already supported.

---

## 2. Recognition models

### 2.1 Pipeline
**Steps:** camera frame → person detector → tracker (keeps IDs over time, corrected for camera movement
using FreeD) → identity (body re-ID, faces) → extras (face direction, sit/stand pose) → framing
controller → VISCA.

### 2.2 Person detection (state of the art, late 2026)
Speeds are on an NVIDIA T4 with TensorRT FP16; a modern RTX card is faster.

| Model | COCO accuracy / speed | Licence | Notes |
|---|---|---|---|
| **YOLO26** (Ultralytics) | 40.9–57.5 AP at 1.7–11.8 ms; Medium 53.1 AP at 4.7 ms | AGPL-3.0 (fine for this personal church project); paid Enterprise licence only for company use | No NMS step, huge ecosystem, detection + pose + tracking in one library. **Recommended** |
| **RF-DETR** (Roboflow, ICLR 2026) | Medium: 54.7 AP at 4.4 ms; 2XL: ~60 AP | Apache 2.0 for Nano–Large; XL/2XL under Roboflow's PML licence | Transformer with a DINOv2 backbone, no NMS step, fine-tunes well. Slightly more accurate at the same speed; benchmark it against YOLO26 |
| **D-FINE** | up to ~55.8 AP | Apache 2.0 | DETR family, solid |
| **DEIMv2** | up to 57.8 AP (X) | check (DEIM v1 is Apache 2.0) | Built on DINOv3 |

Takeaways:
- **Speed is not the problem.** 4 cameras × 25 fps = 100 frames per second, about 10 ms each.
  A mid-range RTX handles RF-DETR Small/Medium batched across cameras.
- **We only need "person".** The real gains come from fine-tuning on the venue's own stage footage: dark
  stage, spotlights, backlit screens, rows of audience heads.
- **Stage problems to plan for:**
  - tiny people in wide shots (higher input resolution or tiling when zoomed out)
  - sudden light changes
  - LED walls and posters showing people (exclusion areas help)

### 2.3 Tracking (keeping IDs from frame to frame)
- **ByteTrack:** fast and simple, a very good default.
- **OC-SORT:** handles curvy motion and short occlusions better.
- **BoT-SORT:** adds camera-motion compensation and appearance features; most accurate of these
  (~65 HOTA on MOT17).
- **Deep OC-SORT:** OC-SORT plus appearance, close to BoT-SORT.
- **Libraries:**
  - Ultralytics has BoT-SORT (the default, with optional ReID) and ByteTrack built in, and they work
    with YOLO26 detection and pose models.
  - Roboflow `trackers` (Apache 2.0) and BoxMOT (AGPL, fine for this project) have more trackers to
    compare.
- **Our advantage:** FreeD tells us exactly how the camera moved. We can turn every detection into room
  angles before tracking, which removes most of the moving-camera problem that BoT-SORT has to guess
  from the image.

### 2.4 Keeping the right person locked
- **Body re-ID:** OSNet (~2.2 M parameters, torchreid). Store a gallery of the target's appearance
  features and use it to find them again after occlusion or loss.
- **Cheap extra clues:** clothing colour, height, position continuity.
- **Face recognition** whenever the face is visible: a strong confirmation of identity.

### 2.5 Faces (registered-face tracking and face direction)

| Need | Option | Licence | Notes |
|---|---|---|---|
| Find faces + 5 landmarks | **YuNet** (OpenCV Zoo) | permissive (check the zoo's LICENSE file) | A few ms on CPU; weaker on hard faces |
| | SCRFD (InsightFace) | code MIT, **weights non-commercial** | More accurate |
| Recognise registered people | **SFace** (OpenCV Zoo) | Apache 2.0 | Commercially usable; fine for 7 known people |
| | ArcFace / buffalo_l (InsightFace) | **non-commercial**, paid licence available | Most accurate |
| | AdaFace | code MIT; check training-data terms | Strong on low-quality faces |

- **Licence note for a church project:** InsightFace's free models are for non-commercial *research*.
  Church use is non-commercial but not research, so it's still unclear. SFace has no such limit.
- **Face direction:** work it out from the landmarks (nose vs eyes), which is cheap, or use a
  head-pose model such as 6DRepNet (check licence).
- **Privacy (EU/Finland):** face templates used to identify people are biometric data under GDPR
  (Article 9). Plan for:
  - explicit consent from each registered person
  - local storage only
  - a one-click wipe

### 2.6 Body pose (sit/stand, raised hands, body direction)
- **YOLO26-pose** (Ultralytics): runs in the same library as the detector and works with its trackers.
  The simplest choice now that AGPL is fine.
- **Alternatives:** RTMPose (detects each person first, then keypoints) or RTMO (single stage; faster
  with many people).
  - RTMO-l: 74.8 AP on COCO, 141 fps on a V100.
  - OpenMMLab MMPose code is Apache 2.0; check the weights.
- **Sit/stand:** hip–knee angles plus how far the head drops, measured in room angles thanks to FreeD.

### 2.7 Who is speaking: mic levels from the Behringer mixer
The mics don't reach vMix separately, but Behringer mixers share live channel meters over the network.
The tracker can read them straight from the mixer, the same way Behringer's own editor apps do.

| Mixer family | Protocol | How meters are read | Notes |
|---|---|---|---|
| X32 / M32 | OSC over UDP, port 10023 | Subscribe with `/meters/...`, renew with `/xremote` about every 9–10 s | Replies go back to the port the request came from |
| **X Air (XR12/16/18, X18) and Midas MR12/MR18: the mixers in use** | OSC over UDP, port 10024 | Send `/meters` with `/meters/1`, repeat every ~10 s (details below) | Values in 1/256 dB |
| WING | Native protocol (TCP 2222 + a UDP port for meters), or OSC on 2223 | Request meters and renew every few seconds | Native protocol allows up to 16 clients, OSC only one subscriber, so native is better. `libwing` is a reference implementation |

**The mixers in use (X Air + Midas MR18).** The MR18 is Midas's version of the XR18 and uses the same
protocol, so one connector serves both. The app can also listen to more than one mixer at a time.
- **Connection:** OSC over UDP to the mixer's IP, port 10024. Replies come back to the port we sent from.
- **Live levels:** send `/meters` with the arguments `/meters/1` and `0`. The mixer then streams levels
  for about 10 seconds, so the app repeats the request every 9.5 s.
  - **Data format:** a 4-byte count (40, little-endian), then 40 little-endian 16-bit integers in
    1/256 dB.
  - **Order of the 40 values:**
    - 1–16: input channels 1–16
    - 17–26: Aux L/R and FX returns 1–4 (L/R)
    - 27–36: buses 1–6 and FX sends 1–4
    - 37–40: main L/R and monitor L/R
  - **Confirmed:** Bitfocus Companion's MR18/X Air module does exactly this (source read 2026-10-31).
    That settles the doubts about value order and byte format from the forum posts.
- **Channel info:** `/ch/01/config/name` (name), `/ch/01/mix/on` (mute), `/ch/01/mix/fader` (fader).
  Companion sends `/xremote` and `/-local/updrate 1` in the same 9.5 s heartbeat, so the mixer pushes
  changes. That way the app shows the mixer's own channel names and knows when a mic is muted.
- **Smoothing:** Companion averages the last 10 meter readings before showing a value. We'll need our
  own attack/hold logic anyway (see below).
- **Code to learn from:**
  - **Companion's `companion-module-behringer-xair`** (MR18, XR12/16/18): the clearest reference
  - `xair-api` (Python, tested on an MR18)
  - `behringer-mixer` (async Python)
  - `xair-client` (TypeScript, includes a meter parser)
  - `dc-xair-cli` (has a `meters` command)
- **Source caveat:** Behringer's official protocol PDF returned a server error on 2026-10-31. The details
  above come from Companion's module source and those libraries.

How audio-follow would work:
1. **Mapping:** in the app, map a mixer channel to a person (registered face), a camera preset or a seat
   area, and choose which camera(s) may respond. The mixer's channel names appear in the list, with
   live meters next to them.
2. **Detecting speech:**
   - "Speaking" means the level stays above a threshold for a moment (~0.3 s).
   - A hold time (~2 s) stops pauses from flipping the camera back and forth.
   - Muted channels, and channels with the fader down, are ignored.
   - An optional priority list decides who wins when two people talk at once (e.g. the pastor's mic).
3. **Reacting:** the assigned camera goes to that person's preset or area, and tracking locks onto them.
   - **Tally-aware:** if possible, a camera that is *not* on Program picks up the new speaker, so the
     director can cut to it.
   - If only the live camera can respond, it moves with the slow on-air profile, or waits until it's
     off Program (a setting; see 3.4).
4. **Manual control always wins:** an operator's pick or joystick move overrides audio-follow until
   released.

Audio-visual speaker-detection models (Light-ASD / LR-ASD, LoCoNet, LASER-ASD) stay an optional extra,
for panels where several people share one mic.

### 2.8 Running the models on Windows
- **ONNX Runtime GPU with the TensorRT provider:** turn on FP16 and the engine cache. Engines are built
  per GPU and per version, and the first build takes a while. CUDA provider as fallback, DirectML for
  non-NVIDIA cards.
- **Native TensorRT engines** (built with `trtexec`) can be noticeably faster in some setups
  (reports of 30%+). Benchmark both.
- **NVIDIA DeepStream** is Linux-oriented, so it's a poor fit next to vMix on Windows.
- **If vMix runs on the same PC,** it competes for the GPU. Test this, or give the tracker its own PC.

---

## 3. Software stack

### 3.1 Options

| Option | Engine | UI | Pros | Cons |
|---|---|---|---|---|
| **A, recommended** | Python 3.12+: NDI via **cyndilib** (MIT), ONNX Runtime/TensorRT, OpenCV, asyncio UDP for VISCA/FreeD, FastAPI + WebSocket | Web UI in TypeScript (React or Svelte); open in a browser or wrap in a desktop window (Tauri 2 / WebView2) | Fastest to build and tune, best ML tools, works from tablets, easy API for vMix/Companion | Python needs discipline for real-time work: one process per camera, nothing heavy in the control loop |
| B | C++ or Rust engine: native NDI SDK and TensorRT | Qt/QML | Maximum performance, lowest latency | Much slower to develop; ML experiments are harder |
| C | C#/.NET 8: NDI .NET, ONNX Runtime C# | WPF / WinUI 3 / Avalonia | Strong Windows desktop UI | Smaller ML ecosystem; Windows only |

- **Hybrid route:** start with A. If one piece turns out too slow (for example frame handling), move
  only that piece to native code.
- **Existing proof:** AutoPTZ is built like this: Python + PySide6 + ONNX Runtime (TensorRT/CUDA/DirectML),
  one worker per camera.

### 3.2 Architecture sketch (input for a later plan, not the plan)
- **Per camera:**
  - NDI receiver → newest frame only (never queue old frames)
  - detector (GPU shared across cameras, batched) → tracker
  - target choice and rules (areas, auto select, faces) → framing controller → VISCA
  - a FreeD listener keeps the camera's live position up to date
- **Control loop at 30–60 Hz:**
  - prediction to cover video delay (Kalman filter)
  - PD control plus target-speed feed-forward, dead zone
  - slower moves when zoomed in
  - acceleration limits, so moves look like a human operator's
- **What the research says:**
  - Kalman-based feed-forward beats plain feedback when there is delay.
  - Interacting-multiple-model Kalman filters have been used with off-the-shelf IP PTZ cameras.
  - Feed-forward on target speed plus PI control improves PTZ tracking.
  - AutoPTZ uses one-euro smoothing + PD + velocity feed-forward + quiet zone.
- **Video to the UI:** compressed preview frames (JPEG over WebSocket, or WebRTC) at ~10–15 fps.
  All overlays (areas, silhouette, face frames) are drawn in the browser from tracking data, so they
  stay sharp and draggable.
- **Settings:** JSON or SQLite: 5 App Settings per camera, areas as room angles, face gallery.
- **API:** WebSocket/HTTP for the UI, plus HTTP/OSC for vMix scripts and Companion/Stream Deck.

### 3.3 vMix integration
- **TCP API, port 8099:**
  - `SUBSCRIBE TALLY`: per input, 0 = off, 1 = program, 2 = preview.
  - `SUBSCRIBE ACTS`: all activator events.
  - XML state: inputs, titles, audio meters.
- **HTTP API, port 8088:** functions and scripts, for example starting or stopping tracking from a vMix shortcut.
- **vMix can move PTZ cameras itself.** If operators also use vMix PTZ, our app must notice through
  FreeD and step aside.

### 3.4 Smoother, slower moves while a camera is on Program (chosen)
- **Knowing what's live:** vMix's `SUBSCRIBE TALLY` reports every input's state (off / program /
  preview) the moment it changes. Each camera is linked to its vMix input; the app can suggest the link
  by matching the input's NDI source name.
- **On-air profile:** each App Settings slot gets a second set of motion limits, used while the camera
  is on Program:
  - lower top speed and gentler starts and stops (S-curve acceleration)
    - As built: the on-air profile scales the top speed and the acceleration (a constant acceleration
      limit). The S-curve exists since 2026-11-04 (`Motion.jerk_time`), but only above Pan/Tilt
      Smoothness 5, off air and on air alike: the acceleration builds up over 0.06 s at 6 to 0.3 s at
      10. At 1-5 starts and stops ramp at a constant acceleration (progress.md, 2026-11-04).
  - a bigger dead zone, so small movements of the speaker are ignored
  - slow zoom, or no zoom, while live
  - no big re-targeting moves (e.g. jumping to a new speaker): wait until off Program, or move very
    slowly (a setting)
- **Off air** (preview, or not used): the normal, quicker profile, so the camera re-frames fast and is
  ready when the director cuts to it.
- **Profile changes are blended** over about a second, so the switch itself isn't visible.
- **Without vMix:** a manual "on air" toggle per camera in the app (and via Companion/OSC) does the same job.
- **Camera limit:** how slow and smooth the slowest moves can be depends on the P400's lowest speed
  steps. That's test 1 in section 1.5.

---

## 3.5 Statistics: how each number is really measured (researched and tested 2026-10-31)
Rule: every number is measured, never estimated. Each one has a known source, and the UI says what
it means.

**How vMix does it (the model for our Statistics button and page):**
- **Bottom bar:** render time (GPU milliseconds per frame; "consistently above 20 ms" means the GPU is
  at its limit), GPU Mem, CPU used by vMix, total CPU (keep under 70 %), and optional red
  "CPU/GPU overloaded" warnings.
- **Statistics window, per input:**
  - *Source Dropped*: frames lost because the camera or source was slow.
  - *Renderer Dropped*: frames lost because the GPU was slow.
  - *Resync*: frames dropped because the camera's clock runs fast.
  - *Video*: the real arrival gaps of the last frames, e.g. `33 33 33 33 (49)`, where the number in
    brackets is how long the frame took to show.
  - A *Reset* button zeroes the counters.

**Our sources (tested on this PC):**

| Number | Source | Notes |
|---|---|---|
| GPU load (NVIDIA) | NVML `utilization.gpu` | Share of time any GPU work was running in the last ~1/6–1 s. It isn't "how full" the GPU is. Measured: 4 % idle, 40–46 % while detecting 220 images/s |
| Our app's GPU share | NVML per-process utilization (`nvmlDeviceGetProcessUtilization`) | Works on Windows here |
| 3D / video engine load (vMix's share) | Windows "GPU Engine" counters (what Task Manager uses) | Our CUDA work is **not** counted there on this PC (hardware-accelerated GPU scheduling), so these show the D3D/video load from vMix and others |
| Our app's video memory | Windows "GPU Process Memory → Dedicated Usage" per process | Measured 404 MB for the detector |
| GPU temperature, total video memory | NVML | |
| CPU total / our app | `psutil` (system, and the sum over the app's processes) | Same idea as vMix's "Total" and "CPU vMix" |
| Network in/out | `psutil` per-interface byte counters, divided by the measured interval | |
| NDI dropped frames | NDI SDK receiver performance data (total and dropped frames), via cyndilib | Equivalent to vMix's *Source Dropped* |
| Frame arrival gaps | Arrival time of every new frame, `perf_counter` | Shown like vMix: last gaps in ms |
| Detector skipped frames | Frames that arrived while the detector was still busy | Equivalent to vMix's *Renderer Dropped* |
| Detection time | `perf_counter` around resize, inference and decoding, separately | Shown as median / 95th percentile / max per interval, not averages |
| Pipeline delay | Frame arrival → detection result, measured in the vision worker with `perf_counter` | Not with `time.monotonic()`: on Windows (Python 3.12) it ticks only every 15.6 ms, so delays came out as 0, 16, 31 or 47 ms (found and fixed 2026-11-01). The monotonic clock is still the time base shared with the core (frame time vs camera position) |
| VISCA round trip | Command sent → camera's ACK | Plus counts of timeouts and errors |
| FreeD | Packets per second, age of the newest packet, checksum errors | |
| Glass-to-glass delay | **Calibration test**, not live: move the camera, compare when FreeD reports the move with when the picture moves | Shown with the date it was measured |

## 4. Existing products (to learn from)

| Product | Works with P400? | Notes |
|---|---|---|
| Panasonic AW-SF100 / SF200 | No, Panasonic only | Motion + optional face recognition, fully offline, server version for several cameras, from ~$1,575 |
| BirdDog X1 / X1 Ultra | No, built into those cameras | AI tracking and auto framing in the camera |
| **TrackPilot** (free, Windows, v1.7.2, Oct 2026) | Probably (NDI + VISCA, many brands) | Click to track, auto-zoom, group mode, profile per speaker, tracking and no-go areas, joystick, Companion, several cameras |
| **AutoPTZ** (open source, AGPL-3.0) | Probably (VISCA-IP, NDI) | Python/PySide6/ONNX Runtime; face + re-ID identity; good architecture reference. Our app can be AGPL too, so its code could be reused with credit |

**Assessment:** TrackPilot is free and covers part of this already. It's worth testing on one P400 as
a baseline. What our version adds:
- The full feature set: area rules, 5 profiles per camera, setup wizard, preset link,
  registered-face priority, multi-person framing, face direction, sit/stand.
- Exact FreeD positions.
- vMix-aware behaviour.
- A UI of its own, fully under the project's control.

---

## 5. Nice-to-have features
Effort: easy / medium / hard.

**Production integration**
1. **Tally-aware motion: CHOSEN** (easy): on Program, only slow, small corrections; on Preview or off
   air, fast re-framing and big zoom changes are allowed. See 3.4.
2. ~~Automatic lower thirds~~: not wanted (2026-10-31).
3. **Audio-follow: CHOSEN** (medium): X Air / MR18 mixer levels decide who is speaking; that person
   becomes the target, or a camera is sent to their preset or seat. See 2.7.
4. **Director suggestions** (medium/hard): show which camera has the best shot right now (stable, face
   visible, well framed); optionally cut in vMix.
5. **Speaker queue / rundown** (medium): list the show's speakers; "Next speaker" sends a camera to their
   seat or podium and arms face tracking for them.
6. **Q&A mode** (medium): find an audience member who stands up or raises a hand, and frame them.

**Smarter tracking**

7. **Multi-camera hand-off** (hard): FreeD plus a one-time calibration means the cameras know where each
   other is looking, so picking a person on one camera can send another camera to the same person
   (wide + tight pair).
8. **Wide-camera assist** (medium): one camera stays wide and watches the whole stage; it guides the
   others so they never lose the target.
9. **Operator-style moves** (medium): smooth S-curve starts and stops, look-room, rule-of-thirds options,
   letting a shot settle like a human operator would. (S-curve starts and stops: built 2026-11-04, with
   Pan/Tilt Smoothness above 5.)
10. **Lens calibration wizard** (medium): measure field of view against zoom for each camera, for exact
    click-to-centre and smooth control.
11. **Virtual PTZ fine-tuning** (hard): small corrections done as a crop inside the 4K picture, sent to
    vMix as our own 1080p NDI output, for perfectly smooth micro-moves. Heavy on GPU and network.

**Operator tools**

12. **Joystick/gamepad and BirdDog keyboard override** (easy/medium): tracking pauses on manual moves and
    resumes afterwards.
13. **Stream Deck / Bitfocus Companion** (easy over HTTP/OSC; a custom module is medium): start/stop,
    switch App Settings, pick the target.
14. **Tablet and phone control**: comes free with a web UI.
15. **Hotkeys and a panic button** (easy): stop every camera at once.
16. **Show files** (easy): save and load a whole venue setup (areas, presets, faces, profiles).

**Reliability**

17. **Health panel** (easy): NDI fps and delay, FreeD alive, GPU load, model time per camera, VISCA errors.
18. **Failsafe** (easy, really a must-have): if video or the tracker freezes, stop the camera and hold the shot.
19. **Record and replay** (medium, very useful during development): record NDI + FreeD + decisions, then
    replay offline to tune settings without cameras.
20. **Session log / timeline** (easy): who was on which camera when; could produce chapter markers.

**Setup**

21. **Camera auto-discovery** (easy): NDI finder plus BirdDog REST `/about`.
22. **Guided setup wizard per camera** (medium): plus venue templates.
23. **Face-data privacy tools** (easy): consent note, expiry date, one-click wipe.

---

## 6. Hardware and network notes
- **Bandwidth:** Full NDI 4K is up to 360 Mbps per camera, so all 4 into one PC is roughly 1–1.5 Gbps.
  Either use a 10 GbE network card, run NDI at 1080p, or use NDI's low-bandwidth streams for tracking
  (if their delay is OK).
- **Shared PC:** the tracker sometimes runs on the vMix PC. Then:
  - **GPU:** vMix and the tracker share it. An RTX 40-series or newer is plenty for the tracker alone,
    but the app needs a "light" mode: smaller model, fewer detections per second, maybe fewer cameras.
  - **Network:** both programs pull video from the cameras. Use NDI low-bandwidth streams or capture
    cards for the tracker so the load doesn't double.
  - **Monitoring:** the app should show its GPU use, so the operator sees if vMix is getting starved.
- **Firmware:** update all four P400s to the latest (5.5.115 / MCU 035 per BirdDog's knowledge base).
- **Fixed IPs:** VISCA and FreeD use UDP, so give the cameras and the tracking PC fixed addresses.

## 7. Open questions
None: the requirements at the top settled them on 2026-10-31. The build plan is plan.md.

---

## Sources
- BirdDog P400 tech specs: https://birddog.tv/p400-overview/p400-techspecs/
- BirdDog P400 user guide (March 2023): https://birddog.tv/wp-content/uploads/2023/03/BirdDog_P400_User-Guide_230330.pdf
- BirdDog camera release notes 5.5.094: https://birddog.tv/wp-content/uploads/2023/01/BirdDog-General-Camera-Release-Notes_5.5.094_230831.pdf
- BirdDog firmware notes (knowledge base): https://desk.birddog.tv/portal/en/kb/articles/birddog-camera-firmware-release-notes
- BirdDog automation / API page: https://birddog.tv/api/
- BirdDog RESTful API 2.0 (P-series): https://birddog.tv/AV/API/index.html
- BirdDog X4 VISCA-over-IP commands: https://birddog.tv/x4-series-visca-over-ip-commands/
- BirdDog XL VISCA-over-IP commands: https://birddog.tv/xl-visca-over-ip-commands/
- NDI PTZ and control messages: https://docs.ndi.video/all/developing-with-ndi/ndi-metadata/ptz-and-control-messages
- NDI receive (bandwidth modes): https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-recv
- cyndilib docs: https://cyndilib.readthedocs.io/en/stable/examples.html
- FreeD (Vizrt description): https://documentation.vizrt.com/tracking-hub-guide/1.2/Description_of_the_FreeD_protocol.html
- FreeD (open-source implementation): https://github.com/stvmyr/freeD
- RF-DETR: https://github.com/roboflow/rf-detr
- RF-DETR paper: https://arxiv.org/pdf/2511.09554
- YOLO26 paper: https://arxiv.org/pdf/2601.12882
- RF-DETR vs YOLO26: https://codersera.com/blog/rf-detr-vs-yolo26-object-detection-comparison-2026/
- Roboflow detector comparison: https://roboflow.com/blog/rf-detr-vs-alternatives
- DEIMv2 paper: https://arxiv.org/pdf/2509.20787
- JetBrains detector overview (2026): https://blog.jetbrains.com/pycharm/2026/07/best-object-detection-models-for-machine-learning-in-2026/
- Ultralytics licensing: https://docs.ultralytics.com/ and https://www.ultralytics.com/it/license
- Ultralytics tracking (BoT-SORT, ReID, pose tracking): https://docs.ultralytics.com/modes/track
- Tracker comparison: https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/multi-object-tracking-deepsort-bytetrack-ocsort
- Roboflow trackers: https://trackers.roboflow.com/latest/ and https://pypi.org/project/trackers/2.3.0/
- OC-SORT: https://github.com/noahcao/OC_SORT
- OSNet paper: https://arxiv.org/pdf/1910.06827
- torchreid: https://github.com/KaiyangZhou/deep-person-reid
- InsightFace commercial licensing: https://www.insightface.ai/solutions/face-recognition-licensing
- Free face detection models overview: https://www.edenai.co/post/top-free-face-detection-tools-apis-and-open-source-models
- YuNet: https://assets.transloadit.com/devtips/real-time-face-detection-using-yunet-and-cli-automation/
- RTMO paper: https://ar5iv.labs.arxiv.org/html/2312.07526
- Light-ASD: https://github.com/Junhua-Liao/Light-ASD
- LoCoNet paper: https://arxiv.org/html/2301.08237v2
- LASER ASD: https://pypi.org/project/laser-asd/
- ONNX Runtime TensorRT provider: https://onnxruntime.ai/docs/execution-providers/TensorRT-ExecutionProvider.html
- Triton discussion (ORT-TensorRT vs native TensorRT): https://github.com/triton-inference-server/server/discussions/8868
- Tauri vs Electron: https://dev.to/gethopp/tauri-vs-electron-performance-bundle-size-and-the-real-trade-offs-1el4
- PTZ control papers:
  - IMM Kalman on IP PTZ: https://www.scitepress.org/PublishedPapers/2014/46741/46741.pdf
  - TU Wien latency/feed-forward study: https://repositum.tuwien.at/handle/20.500.12708/214990
  - Kinematics feed-forward PTZ control: https://pure.bit.edu.cn/en/publications/research-on-ptz-tracking-control-system-based-on-kinematics-feedf
  - Avigilon delay-compensation patent: https://patents.google.com/patent/US11240403
- vMix TCP API: https://vmix.com/help28/TCPAPI.html
- Panasonic auto tracking: https://info.panasonic.com/us/PTZ-autotracking
- BirdDog X1 with AI tracking: https://videoguys.com/blogs/news-and-sales/birddog-is-back-introducing-the-birddog-x1-x1-ultra-and-maki-ultra
- TrackPilot: https://trackpilot.live/
- AutoPTZ: https://github.com/AutoPTZ/autoptz
- Third-party tracking tools: https://github.com/AVStreamAI/autotrack-any-ptz-camera
- Behringer X32 OSC (community):
  - https://www.sites.google.com/site/patrickmaillot/x32
  - https://openstagecontrol.discourse.group/t/receive-osc-from-x32/531
  - https://github.ddaodan.workers.dev/maolan/mixosc
- Behringer X Air remote control protocol (server error on 2026-10-31): https://cdn.mediavalet.com/aunsw/musictribe/hmivS7F05kKSJlWDYPGLpA/agTKkSIWS0y1bJl97YZEjg/Original/X%20AIR%20Remote%20Control%20Protocol.pdf
- X Air meters (live XR18 tests): https://github.com/notameadow/xair-osc
- xair-client (TypeScript, meter parser): https://github.com/rocklobster42195/xair-client
- X Air meter forum threads: https://behringer.world/viewtopic.php?t=2326 and https://behringer.world/viewtopic.php?t=1545
- Bitfocus Companion MR18/X Air module (meter handling in index.js and buildMeterDefs.js): https://github.com/bitfocus/companion-module-behringer-xair
- xair-api (Python, MR18): https://github.com/onyx-and-iris/xair-api-python
- behringer-mixer (Python): https://pypi.org/project/behringer-mixer/
- dc-xair-cli: https://pypi.org/project/dc-xair-cli/
- Behringer WING remote protocols: https://cdn.mediavalet.com/aunsw/musictribe/M7u1XZvRGkWxJAVwCKE5BA/7qQ-l43rn0SEnqyb5ZcETg/Original/WING%20Remote%20Protocols_V3.0.6-30.pdf
- WING community notes: https://behringer.world/viewtopic.php?p=14620 and https://behringer.world/viewtopic.php?p=14236
- libwing: https://docs.rs/libwing
- blackmagic-io: https://github.com/nick-shaw/blackmagic-io
- pydecklink: https://pypi.org/project/pydecklink/0.3.3/
- DeckLink capture latency (vvvv): https://legacy.vvvv.org/contribution/vvvv.nodes.decklink
- pymagewell: https://pypi.org/project/pymagewell/
- Magewell catalogue: https://www.magewell.com/files/product-catalogue-2017Q3.pdf
- vMix NDI outputs: https://1.vmix.com/ndi and https://streamgeeks.us/using-ndi-with-vmix/
- vMix Statistics window: https://www.vmix.com/help29/Statistics.html
- vMix bottom-bar statistics: https://www.vmix.com/knowledgebase/article.aspx/227/what-do-the-usage--performance-statistics-in-the-bottom-left-corner-of-vmix-mean
- vMix high render times: https://www.vmix.com/knowledgebase/article.aspx/147/diagnosing-high-render-times
- NVML utilization definition: https://docs.nvidia.com/deploy/nvml-api/api/structnvmlUtilization__t.html
- GPU utilization vs saturation: https://arthurchiao.art/blog/understanding-gpu-performance/
- Windows GPU Engine counters per process: https://learn.microsoft.com/en-us/answers/questions/5641645/how-to-get-the-special-process-gpu-usage-with-the
- PDH GPU counter parsing (examples):
  - https://docs.rs/gpuviewer-core/latest/gpuviewer_core/wddm/pdh/index.html
  - https://docs.rs/crate/gpur/0.1.0/source/src/backend/windows.rs
- NDI receiver performance (dropped frames): https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-recv

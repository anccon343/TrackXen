# TrackXen user guide

TrackXen points up to four BirdDog P400 cameras at the people on stage by itself: each camera
follows its own person. It works like Canon's Auto Tracking Application, with vMix tally and
microphone-follow added. This guide is for running it; the technical notes are in `progress.md`.

**Safety first:** the red **STOP ALL** button (top right, on every screen except the PIN screen)
switches Auto Tracking and Audio follow off on every camera and stops every camera at once. Keep a
person near it whenever TrackXen moves cameras in front of people. After STOP ALL, switch Audio follow
on again per camera (tracking panel) when you want it. If the button can't reach TrackXen, a message
says so: press it again.

## 1. Installing and starting

- Run the setup program `TrackXen-Setup-0.1.0.exe`. Windows may say "Windows protected your PC"
  (the program isn't signed): **More info** → **Run anyway**. Choose **Install for all users** (needs
  an administrator; also lets tablets and the cameras' FreeD position data through the Windows
  Firewall, on private networks) or **only for me** (no administrator; Windows then asks about the
  firewall on the first start: allow private networks).
- An NVIDIA RTX card is recommended; without one TrackXen runs on the processor and can only follow
  people slowly.
- TrackXen starts in the background (an orange ring in the notification area, near the clock) and
  opens its page in the browser. Right-click the ring: **Open TrackXen**, **Open the logs folder**,
  **Quit TrackXen**. Starting TrackXen again while it runs just opens its page.
- If TrackXen can't start (for example another program uses port 8090), a message says why and
  there is no ring.
- Tablets and phones on the same network: open `http://<this PC's address>:8090`, or the PC's name
  (`http://<PC name>:8090`). Other names, such as an alias set up on the router, are refused.
- **The first start needs the internet:** TrackXen downloads NVIDIA's graphics libraries for this
  card (about 1.5 GB, once per PC; the welcome page shows the progress). Then it prepares the
  graphics engine for the card (2-10 minutes). TrackXen already works meanwhile, more slowly.

## 2. First start: the welcome page

1. **This PC:** check that the graphics card is found. "Test this PC's speed" measures it and
   recommends Light, Auto or HYPER; press **Use it**.
2. **Cameras:** press **Find cameras**. The P400s appear (NDI must be on in each camera, in BirdUI).
   Tick them. PTZOptics cameras: type the IP address under "Cameras without NDI", **Test**, then **Add
   to the list**. Up to four cameras in all; press **Add**.
3. **vMix and the mixer** (optional, see section 6).

For exact positions, set each P400's FreeD output (BirdUI → CamControl → FreeD) to this PC's
address; Settings → Cameras shows which port each camera uses.

A camera can also come in through an **HDMI/SDI capture card** instead of NDI: Settings → Cameras →
"Video from: HDMI / SDI capture card" and its device number. It is still controlled over the network.

### PTZOptics cameras (G2, e.g. PT20X-SDI-GY-G2)

They have no NDI and no FreeD; TrackXen takes their **own network stream** (RTSP) and asks them for
their position 20 times a second over VISCA.

- **In the camera's web page** (type its IP address in a browser):
  - VISCA over UDP on, port **1259** (the default)
  - the RTSP stream on, without a login (the default)
  - recommended for the least delay: H.264, 1920×1080 at 30 fps (or 1280×720 on the sub stream),
    I-frame interval 30
- **Settings → Cameras:** "Camera type: PTZOptics G2" fills everything in. The video comes from
  `rtsp://<camera IP>:554/1` (main stream); **Sub stream** switches to `/2`. **Test camera** shows
  whether it answers and what its video is like.
- **VISCA over UDP** works next to vMix or a joystick. The camera accepts only **one** TCP control
  connection, so don't choose TCP when vMix also controls it over TCP.
- **Once per camera, with someone there (it moves):** measure its numbers. Until then TrackXen uses
  the data sheet's.
  ```
  .venv\Scripts\python tools\visca_probe.py <camera IP> --transport udp calibrate --yes
  .venv\Scripts\python tools\visca_probe.py <camera IP> --transport udp zoom-range --yes
  .venv\Scripts\python tools\stream_probe.py rtsp://<camera IP>:554/1 --delay <camera IP> --yes
  ```
  Then enter the units per degree and the video delay in Settings → Cameras. Clear the camera's
  pan/tilt limits before `calibrate`, or it measures the limits instead of the end stops.
- A network stream has more delay than NDI or a capture card. If the PTZOptics cameras later go into
  vMix over SDI, vMix can also pass them on as NDI (vMix External Output / NDI). That is the quickest
  way into TrackXen.

## 3. Everyday use (the Cameras page)

- The four pictures show what each camera sees. A red border and **LIVE** = that camera is on
  Program in vMix.
- Click a camera to control it on the right. **Auto Tracking** on/off; the **Status** shows: No target
  selected, Searching, Tracking, Angle Fixed, Target lost.
- **Pick the person** by clicking them in the picture ("Tracking Target Select" mouse mode). With Auto
  Select or registered faces switched on, TrackXen picks by itself.
- **App Settings 1-5:** five complete tracking setups per camera (for example "Sermon", "Worship",
  "Panel"). Switch with the five buttons; changes work at once and **Save** keeps them. Other saves
  (faces, connections, a vMix input, Apply in Settings) also keep the changes made so far.
- The **PTZ** tab moves the camera by hand. Moving a camera by hand (here, in vMix, with a joystick)
  stops tracking, unless "Restart Tracking After Manual Operation" is on (wizard step 4).

## 4. Setting up a venue: the Setting Wizard

Press **Setting Wizard** (or **Settings pages** to jump to one page). Each page has the camera picture
with pan, tilt and zoom sliders around it. The frames you draw stay fixed to the room, even when
the camera moves.

1. **Pan/Tilt Operation Control:** stop tilting (Fix Tilt), and the **shooting range**: move the
   camera to each limit and press Capture.
2. **Initial Position:** the view tracking starts from; when the person is lost, keep the view or go
   back here after a few seconds.
3. **Priority Display Area** (always kept in the picture, for example a screen) or **Fixed Viewing
   Angle Areas** (a podium: while the person stands inside, the shot stays still).
4. **Auto Select:** someone standing in the auto select area for the start time becomes the target.
5. **Composition:** where the head sits in the picture (drag the silhouette), how big the person is,
   the quiet zone (Pan/Tilt Halting Area), sensitivity, and the calmer motion while live.
6. **Exclusion Areas:** posters, photos and screens with people on them are never picked.
7. **Tracking Halting Areas:** for example the seats of people waiting their turn: someone walking in
   there is let go, and nobody there is picked.
8. **Auto Framing:** Multi-Person (frame the speaker with the people next to them), Face Direction
   (leave space in front of the face), Sit/Stand (closer when the person sits).
9. **Registered Face Tracking:** register up to 7 faces per camera; only those people are followed.
   Ask people first; the data stays on this PC.
10. **Camera Preset Link:** what tracking does after a camera preset is recalled.

Save a **show file** per venue (Settings → System → Save setup) and load it there next time. Loading
one keeps this PC's own web address, performance mode and hardware test result. A show file saved by
an earlier version with a camera id or FreeD port used twice, or a port of 0, is repaired as it loads:
the later camera gets a free id or no FreeD, the port its default. The message after loading and the
log say what was changed. Other values that are not valid are refused, and nothing is loaded.

**Settings → Apply** checks the settings first: every port 1-65535 (an empty FreeD port means no
FreeD), no two cameras with the same id or FreeD port. **Add camera** picks a free number and FreeD
port. If another device applied settings since this page loaded them, Apply says "The settings were
changed on another device. Reload them and try again." and loads the current settings: make your
changes again. The Audio follow and On Air switches stay as they were over an Apply.

## 5. Registering a face

Wizard page 9 → **Register**. Ask the person to face the camera; zoom in so the face fills the frame
on the picture. When the frame turns **green**, type a name and press **Capture**. Register in the real
conditions (same glasses, similar light). Tick or untick each face per camera, and drag the order:
higher in the list wins when two registered people appear.

## 6. vMix and microphones

- **Settings → vMix & mixer:** the vMix PC's address (127.0.0.1 if vMix runs on this PC), then pick
  which vMix input shows each camera (TrackXen suggests the match). While a camera is live it moves
  more slowly and calmly; set how calm on wizard page 5. The **On Air** switch (Auto / On / Off) on the
  tracking panel does the same by hand.
- **Mixer:** the MR18 / X Air address (or Find mixers).
- **Settings → Audio follow:** switch on the microphones to follow. For each: who uses it (a
  registered face) and, per camera, the preset that shows their place. When that microphone starts
  speaking, a camera that is not live goes there and frames the person. A camera you just moved by
  hand (here, with a preset, in vMix or with a joystick) is left alone for a while.
- A microphone cue still waiting for its person is dropped when you click a person, use the joystick
  or move the camera, recall a preset, switch Auto Tracking off, or switch Audio follow off for that
  camera (or in Settings → Audio follow). A camera still on its way to the mic's preset then doesn't
  start Auto Tracking there. STOP ALL switches Audio follow off on every camera.
- **If vMix is not connected,** each camera keeps its last on-air state until vMix reports again,
  and a problem light says so. Use the **On Air** switch if needed.

## 7. Buttons on a Stream Deck (Bitfocus Companion)

Use Companion's "Generic HTTP" connection: **POST** to `http://<TrackXen PC>:8090/api/command` with
the header `Content-Type: application/json` and one of these bodies (cameras are `cam1`-`cam4`).
If the PIN is on (section 9), also add the header `X-TrackXen-PIN` with the PIN as its value.

| Button | Body |
|---|---|
| STOP ALL | `{"type": "stop_all"}` |
| Auto Tracking on / off | `{"type": "tracking", "camera": "cam1", "on": true}` (or `false`) |
| App Settings 2 | `{"type": "slot", "camera": "cam1", "index": 1}` (index 0-4 = slots 1-5) |
| Camera preset 3 | `{"type": "preset", "camera": "cam2", "number": 3}` (follows the Camera Preset Link) |
| Let go of the target | `{"type": "clear_target", "camera": "cam1"}` |
| On air: on / off / follow vMix | `{"type": "on_air", "camera": "cam1", "on": true}` (`false`, or `null`) |
| Audio follow on / off | `{"type": "audio_follow", "camera": "cam1", "on": true}` |

On/off values are `true` or `false` (`1`/`0` and `"true"`/`"false"` also work); anything else is
refused.

## 8. Statistics and logs

- The bottom bar always shows detection time, GPU and CPU load, and problem lights. **Statistics**
  opens the full table (like vMix's): per camera frame rate, dropped frames, detection times, camera
  replies; **History** shows past days' graphs and events, with downloads.
- A problem light comes on after about 3 s and goes off about 5 s after the problem is gone. Besides
  the frame rate, GPU, CPU and memory lights: a camera not answering VISCA, FreeD position data not
  arriving, vMix or the mixer not connected, and log files that can't be written. GPU load and GPU
  temperature have a light each.
- The log file is written automatically: `%APPDATA%\TrackXen\logs\TrackXen-YYYYMMDD.log`.
- **Settings → Performance:** Light (slower PCs, or vMix on the same PC), Auto, HYPER (best and most
  responsive); the hardware test recommends one.

## 9. A PIN for phones and tablets

Without a PIN, anyone on the church network can open TrackXen's page and move the cameras.
**Settings → System → PIN for other devices**: type a PIN of 4 to 8 digits twice and press **Switch the
PIN on**. Every other device (phone, tablet, another PC) then shows a number pad once; after the right
PIN it stays signed in. The TrackXen PC itself never asks, unless you tick **Ask on this PC too**.

- After 5 wrong tries a device has to wait 30 seconds, then longer each time.
- **Change the PIN** signs every other device out. **Sign out other devices** does that without a new
  PIN, for example after a tablet was lost.
- Changing or switching off the PIN from another device needs the current PIN.
- Signing out, **Change the PIN**, **Sign out other devices** and switching the PIN on also close
  pages already open on other devices; they show the number pad.
- The TrackXen PC needs no PIN only when its page is opened as `localhost` or `127.0.0.1` (the ring's
  **Open TrackXen** does that). Opened by the PC's name or address, it asks too.

## 10. When something is off

| What you see | What to try |
|---|---|
| "NVIDIA libraries missing: no internet connection" | Connect the PC to the internet once; it tries again every minute (or **Download again**) |
| A number pad asks for a PIN | Settings → System on the TrackXen PC: the PIN, or switch it off |
| "No video" on a camera | NDI on in the camera? Same network? Settings → Cameras → Find devices |
| The camera doesn't move | Is the camera's address right? Statistics → VISCA errors. Is Auto Tracking on? |
| Problem light "camera not answering VISCA" | Is the camera on and on the network, and its address and VISCA port right? TrackXen keeps trying; the light goes off once it answers |
| Problem light "FreeD position data not arriving" | As for "Position shows VISCA" below; tracking still works on VISCA positions |
| Problem light "vMix ... not connected" or "mixer ... not connected" | Is vMix running, or the mixer on? Settings → vMix & mixer: the address |
| "The settings were changed on another device" | The page has loaded the current settings: make your changes again and Apply |
| "No video", and the log says "NDI name ... matches several sources" | Settings → Cameras: choose the camera's full NDI name from the list |
| "No video", and the log says "NDI source not found" while the camera is on | Part of an NDI name is taken only as whole words: "BIRDDOG-12345" finds "BIRDDOG-12345 (CAM)", "BIRDDOG-123" doesn't, and "CAM1" never takes "PC (CAM10)". Choose the camera's full NDI name from the list |
| A "settings" warning in the log after a start | settings.json was damaged or held a value no longer accepted, and TrackXen repaired it (a damaged file is kept as `settings.json.broken-<time>`; the last good copy `settings.json.bak` or the defaults are used). Check the settings |
| It follows the wrong person | Click the right one; add exclusion or halting areas; lower the sensitivity |
| Too jumpy | Lower the sensitivity, widen the Pan/Tilt Halting Area, use a fixed area at the podium |
| Too slow to follow | Raise the sensitivity, make the Pan/Tilt Halting Area smaller |
| Tracking stops after someone used the joystick | Wizard page 4: "Restart Tracking After Manual Operation" |
| Everything slow | Settings → Performance → Light, or run the hardware test |
| Tablets can't open the page | Windows Firewall: allow TrackXen on private networks (or install for all users) |
| Statistics → Position shows "VISCA" for a P400 (no FreeD arrives) | The camera's FreeD output must go to this PC's address and the port in Settings → Cameras; Windows Firewall: allow TrackXen on private networks (or install for all users) |

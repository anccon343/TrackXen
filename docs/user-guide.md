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

### Updates from GitHub

Every push to `main` on GitHub builds a new setup program there (GitHub Actions, about 20-40 minutes)
and publishes it as a release named after its build number. The installed TrackXen installs it from
**Settings → System → Updates**:

1. **Once per PC, an access token** (the repository is private): on GitHub, your picture → Settings
   → Developer settings → Fine-grained tokens → Generate new token. Repository access: **Only select
   repositories → TrackXen**; Permissions: **Contents: Read-only**; pick an expiry date. Paste it in
   **GitHub access token** and press **Save token**. It is kept on this PC in
   `%APPDATA%\TrackXen\update.json`, never in the settings or a show file. Make a new one when it
   expires (TrackXen then says "GitHub refused the access token").
2. **Check for updates** shows whether GitHub has a newer build than this one, with its notes.
3. **Download** fetches the setup program (about 600 MB) and checks it against the checksum published
   with it; a file that doesn't match is not used.
4. **Install now… → Stop TrackXen and install:** TrackXen closes (every camera stops), the setup
   program installs the new version and starts TrackXen again, in about a minute. Reload the page
   then. Not while a camera is live: TrackXen refuses while vMix (or the On Air switch) has one on air.
   An install "for all users" makes Windows ask to allow the setup program.

A version built by hand (`packaging\build.ps1`) counts as build 0, so any GitHub build is newer.

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
- Click a camera to control it on the right. The **Tracking** tab starts with what you need during a
  service: **Auto Tracking** on/off (also on each picture's header), the **Status** (No target
  selected, Searching, Tracking, Angle Fixed, Target lost) and the target; then On Air and Audio
  follow; then the App Settings. **Save** stays at the bottom of the panel.
- Above the pictures: the **Mouse** mode (what a click in the picture does) and **View Initial
  Position** for the selected camera. The camera list on the left appears from two cameras on.
- **Pick the person** by clicking them in the picture ("Tracking Target Select" mouse mode). With Auto
  Select or registered faces switched on, TrackXen picks by itself.
- **App Settings 1-5:** five complete tracking setups per camera (for example "Sermon", "Worship",
  "Panel"). Switch with the five buttons; changes work at once and **Save** keeps them. Other saves
  (faces, connections, a vMix input, Apply in Settings) also keep the changes made so far.
- The **PTZ** tab moves the camera by hand; its moves start and stop softly by Pan/Tilt Smoothness
  (section 4). Moving a camera by hand (here, in vMix, with a joystick) stops tracking, unless
  "Restart Tracking After Manual Operation" is on (wizard: Tracking Target Auto Select).

## 4. Setting up a venue: the Setting Wizard

Press **Setting Wizard** (or **Settings pages** to jump to one page). Each page has the camera picture
with pan, tilt and zoom sliders around it. The frames you draw stay fixed to the room, even when
the camera moves.

The ten pages are Canon's, in four groups. **Next** goes through the main steps (1, 2, 3 and 6),
which are enough for most venues; tick **Optional steps too** to go through all ten. The list on the
left always shows every page: ✓ = in use, ! = a main step still to do (the initial position).

**Picture area**
1. **Pan/Tilt Operation Control:** stop tilting (Fix Tilt), and the **shooting range**: move the
   camera to each limit and press Capture.
2. **Initial Position:** the view tracking starts from; when the person is lost, keep the view or go
   back here after a few seconds.

**Choosing the target**
3. **Tracking Target Auto Select:** someone standing in the auto select area for the start time
   becomes the target. Also "Restart Tracking After Manual Operation".
4. **Auto Select Exclusion Areas** (optional): posters, photos and screens with people on them are
   never picked.
5. **Tracking Halting Areas** (optional): for example the seats of people waiting their turn: someone
   walking in there is let go, and nobody there is picked.

**Framing and movement**
6. **Composition:** where the head sits in the picture (drag the silhouette), how big the person is,
   the quiet zone (Pan/Tilt Halting Area), sensitivity, **Pan/Tilt Smoothness** (below), and the
   calmer motion while live (On-Air Motion).
7. **Priority Display Area** (optional; always kept in the picture, for example a screen) or **Fixed
   Viewing Angle Areas** (a podium: while the person stands inside, the shot stays still).
8. **Auto Framing** (optional): Multi-Person (frame the speaker with the people next to them), Face
   Direction (leave space in front of the face), Sit/Stand (closer when the person sits).

**Extras**
9. **Registered Face Tracking** (optional): register up to 7 faces per camera; only those people are
   followed. Ask people first; the data stays on this PC.
10. **Camera Preset Link** (optional): what tracking does after a camera preset is recalled.

### Pan/Tilt Smoothness (beyond Canon)

**Composition → Pan/Tilt Smoothness**, 1-10 per App Settings, default 5: on the wizard's Composition
page and on the Cameras page under Tracking Sensitivity.

- **Higher is softer:** starts, stops and speed changes are gentler. The camera follows the person's
  walking speed smoothed over a longer time and keeps a speed step longer before it changes to the
  next; above 5 it also builds the acceleration up gradually (an S-curve) and accelerates less (at
  10: three quarters as hard).
- **1 is the liveliest:** the camera reacts at once to every change of the person's speed.
- **Off air and on air.** On-Air Motion still works on top of it: while live, its slower top speed,
  softer starts and stops and bigger halting area come on top of the Smoothness, so live stays calmer
  than off air at the same level.
- **It doesn't change** the top speed, how hard the camera corrects (Tracking Sensitivity) or the
  Pan/Tilt Halting Area.
- **The cost of high values:** sudden moves are followed later, mostly in tight shots. Measured in
  the simulator with a person 15 m away in an 8-degree shot: when they stop and start again, 95 % of
  the time the head is within about 17 % of the picture of its position at 5 and 21 % at 10; a brisk
  walker (1.2 m/s) on a live G2, within about 7 % at 5 and 15 % at 10. In wide shots the difference is
  small.
- A **lower Tracking Sensitivity** also calms a tight shot, but lags a fast walker more, and the
  Smoothness adds to that lag: at sensitivity 3 in an 8-degree shot, a brisk walker is within about 7 %
  of the picture 95 % of the time at Smoothness 1 (as before), 10 % at 5 and 19 % at 10 (a G2: 13 % at
  5, 23 % at 10). Combine a low sensitivity with a low Smoothness in tight shots.
- **When a walking person stops,** the camera eases to a stop a little past them (it follows the walk,
  and the picture reaches TrackXen a moment late). Inside the Pan/Tilt Halting Area it stays there
  instead of turning back; in a tight shot the overshoot is bigger than the halting area and it comes
  back. At a **shooting range** edge it always comes back (one short, slow move), so the picture never
  rests past the range, even when the person stops just inside it. A Display Size or Auto Zoom
  zoom-out there runs in one go while the camera pans back inside with it (the picture may show up to
  about a halting area past the edge for the second or two of the zoom).
- With the **Pan/Tilt Halting Area off**, a small quiet zone stays: 0.15 degrees (a G2: 0.46 degrees,
  from its slowest speed), so the tilt doesn't follow the bob of a walking head. On a G2 in a tight
  shot that costs a little framing: a slowly drifting person at 8 degrees is within about 6 % of the
  picture 95 % of the time, against 4 % before (with 44 instead of 122 starts a minute).

**The arrow buttons** (PTZ tab, and around the wizard's picture) are smoothed by the same setting:
TrackXen ramps the pan and tilt speed up and down along an S-curve instead of jumping to the button's
speed. A diagonal stays straight, and a change of direction passes through a stop.

- A speed change takes at most 0.5 s × (Smoothness − 1) / 9: none at 1 (sent as it is), 0.22 s at 5,
  0.5 s at 10. A small change in a wide shot is quicker.
- **Letting go:** pan and tilt are at speed 0 at most that long plus 20 ms after the release reaches
  TrackXen: at once at 1, within 0.24 s at 5, 0.52 s at 10. If the page loses its connection while a
  button is held, the camera stops at most 0.3 s plus the ramp after the last repeat (0.82 s at 10).
- Zoom is not ramped. Moves made in vMix, BirdUI or with a hardware joystick go straight to the
  camera; TrackXen doesn't ramp them.
- **Never ramped:** STOP ALL, switching Auto Tracking off, "No video", closing a camera (Apply), and
  going to a preset, the initial position or a clicked point. These stop the camera, or take over, at
  once.

**Camera tips for smooth moves**

- Neither camera has a setting of its own for how softly it starts and stops (none in the P400's user
  guide or web API, nor in the G2's menus), so TrackXen does it. `tools\visca_probe.py <camera IP> ramp`
  asks a camera whether it has Sony's ramp curve and slow mode anyway; it only asks, nothing moves.
- **P400 firmware:** keep it current, 5.5.115 / MCU 035 or later (5.5.089 improved pan and tilt at
  low speeds).
- **P400 Adaptive P/T** (camera menu, Pan Tilt Zoom) and **G2 SpeedByZoom** slow the camera down when
  zoomed in, which TrackXen already does itself. Before relying on either, run the speed sweep at full
  tele with it on and with it off (`tools/README.md`, lab checklist). If the speeds differ, the camera
  changes the speeds TrackXen asks for, and TrackXen's speed tables are right only with it off.
- **A G2 in very tight shots:** it can't turn slower than about 1.7 degrees per second (data sheet),
  half the picture width per second at full tele. A slowly walking person in a tight G2 shot gets small
  stop-and-go corrections: measured in the simulator at 8 degrees, about 10 starts a minute at
  Smoothness 5 and about 1 at 10, where a P400 makes none. Zoom out a little, raise the Smoothness,
  or use a P400 for the tightest shots.

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
  more slowly and calmly; set how calm on the wizard's Composition page (On-Air Motion). The **On Air** switch (Auto / On / Off) on the
  tracking panel does the same by hand.
- **Mixer:** the MR18 / X Air address (or Find mixers).
- **Settings → Audio follow:** switch on the microphones to follow. For each: who uses it (a
  registered face) and, per camera, the preset that shows their place. When that microphone starts
  speaking, a camera that is not live goes there and frames the person. A camera you just moved by
  hand (here, with a preset, in vMix or with a joystick) is left alone for a while.
- A microphone cue still waiting for its person is dropped when you click a person, move the camera
  with the PTZ tab, recall a preset, switch Auto Tracking off, or switch Audio follow off for that
  camera (or in Settings → Audio follow). A camera still on its way to the mic's preset then doesn't
  start Auto Tracking there. Moving it in vMix or with a joystick on the way isn't noticed: it still
  starts Auto Tracking where it stops (switch Audio follow off for that camera to prevent that). STOP
  ALL switches Audio follow off on every camera.
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
| Too jumpy | Raise Pan/Tilt Smoothness, lower the sensitivity, widen the Pan/Tilt Halting Area (or switch it on), use a fixed area at the podium |
| Too slow to follow | Raise the sensitivity, make the Pan/Tilt Halting Area smaller, lower Pan/Tilt Smoothness |
| The camera moves on briefly after letting go of an arrow button | The soft stop of Pan/Tilt Smoothness: at most 0.52 s at 10. Lower it; at 1 it stops at once. STOP ALL always stops at once |
| A tight shot on a G2 moves in small stop-and-go steps | The G2's slowest speed (section 4, camera tips): zoom out a little, or raise Pan/Tilt Smoothness |
| Tracking stops after someone used the joystick | Wizard page 4: "Restart Tracking After Manual Operation" |
| Everything slow | Settings → Performance → Light, or run the hardware test |
| Tablets can't open the page | Windows Firewall: allow TrackXen on private networks (or install for all users) |
| Statistics → Position shows "VISCA" for a P400 (no FreeD arrives) | The camera's FreeD output must go to this PC's address and the port in Settings → Cameras; Windows Firewall: allow TrackXen on private networks (or install for all users) |

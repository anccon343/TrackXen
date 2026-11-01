# TrackXen user guide

TrackXen points up to four BirdDog P400 cameras at the people on stage by itself: each camera
follows its own person. It works like Canon's Auto Tracking Application, with vMix tally and
microphone-follow added. This guide is for running it; the technical notes are in `progress.md`.

**Safety first:** the red **STOP ALL** button (top right, on every screen) stops tracking on every
camera and stops every camera at once. Keep a person near it whenever TrackXen moves cameras in
front of people.

## 1. Installing and starting

- Run the setup program `TrackXen-Setup-0.1.0.exe`. Windows may say "Windows protected your PC"
  (the program isn't signed): **More info** → **Run anyway**. Choose **Install for all users** (needs
  an administrator; also lets tablets through the Windows Firewall) or **only for me** (no
  administrator; Windows then asks about the firewall on the first start: allow private networks).
- An NVIDIA RTX card is recommended; without one TrackXen runs on the processor and can only follow
  people slowly.
- TrackXen starts in the background (an orange ring in the notification area, near the clock) and
  opens its page in the browser. Right-click the ring: **Open TrackXen**, **Open the logs folder**,
  **Quit TrackXen**.
- Tablets and phones on the same network: open `http://<this PC's address>:8090`.
- **The first start needs the internet:** TrackXen downloads NVIDIA's graphics libraries for this
  card (about 1.5 GB, once per PC; the welcome page shows the progress). Then it prepares the
  graphics engine for the card (2-10 minutes). TrackXen already works meanwhile, more slowly.

## 2. First start: the welcome page

1. **This PC:** check that the graphics card is found. "Test this PC's speed" measures it and
   recommends Light, Auto or HYPER; press **Use it**.
2. **Cameras:** press **Find cameras**. The P400s appear (NDI must be on in each camera, in BirdUI).
   Tick up to four and press **Add**.
3. **vMix and the mixer** (optional, see section 6).

For exact positions, set each camera's FreeD output (BirdUI → CamControl → FreeD) to this PC's
address; Settings → Cameras shows which port each camera uses.

A camera can also come in through an **HDMI/SDI capture card** instead of NDI: Settings → Cameras →
"Video from: HDMI / SDI capture card" and its device number. It is still controlled over the network.

## 3. Everyday use (the Cameras page)

- The four pictures show what each camera sees. A red border and **LIVE** = that camera is on
  Program in vMix.
- Click a camera to control it on the right. **Auto Tracking** on/off; the **Status** shows: No target
  selected, Searching, Tracking, Angle Fixed, Target lost.
- **Pick the person** by clicking them in the picture ("Tracking Target Select" mouse mode). With Auto
  Select or registered faces switched on, TrackXen picks by itself.
- **App Settings 1-5:** five complete tracking setups per camera (for example "Sermon", "Worship",
  "Panel"). Switch with the five buttons; changes work at once and **Save** keeps them.
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

Save a **show file** per venue (Settings → System → Save setup) and load it there next time.

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
  hand is left alone.

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

## 8. Statistics and logs

- The bottom bar always shows detection time, GPU and CPU load, and problem lights. **Statistics**
  opens the full table (like vMix's): per camera frame rate, dropped frames, detection times, camera
  replies; **History** shows past days' graphs and events, with downloads.
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

## 10. When something is off

| What you see | What to try |
|---|---|
| "NVIDIA libraries missing: no internet connection" | Connect the PC to the internet once; it tries again every minute (or **Download again**) |
| A number pad asks for a PIN | Settings → System on the TrackXen PC: the PIN, or switch it off |
| "No video" on a camera | NDI on in the camera? Same network? Settings → Cameras → Find devices |
| The camera doesn't move | Is the camera's address right? Statistics → VISCA errors. Is Auto Tracking on? |
| It follows the wrong person | Click the right one; add exclusion or halting areas; lower the sensitivity |
| Too jumpy | Lower the sensitivity, widen the Pan/Tilt Halting Area, use a fixed area at the podium |
| Too slow to follow | Raise the sensitivity, make the Pan/Tilt Halting Area smaller |
| Tracking stops after someone used the joystick | Wizard page 4: "Restart Tracking After Manual Operation" |
| Everything slow | Settings → Performance → Light, or run the hardware test |
| Tablets can't open the page | Windows Firewall: allow TrackXen on private networks (or install for all users) |

# Reference: what the user wants the tracker to be

The user's target: **Canon's Auto Tracking Application (RA-AT001), rebuilt for BirdDog P400 cameras**:
every feature, adjustment and screen, with a UI in the style of Canon's camera software
(two reference screenshots, section 6).

- Source manual: `canon-RA-AT001-manual.pdf` in this folder (Canon, Sept 2026 edition, 35 pages).
  Read text with `pdftotext -layout` (available in Git Bash, `/mingw64/bin`). The PDF is encrypted, so
  page images need a renderer such as pdf.js (`pdfjs-dist` with `@napi-rs/canvas`).
- Everything below is a summary in our own words, written 2026-10-31. Values marked "ex" are example
  values seen in the manual's screenshots, not confirmed defaults.
- Our setup (answers from 2026-10-31; details in `research.md`):
  - **Cameras:** 4 × BirdDog P400, each tracking a different person on its own.
  - **PC:** RTX 40-series or newer, sometimes the same PC as vMix.
  - **Video:** mostly NDI, but HDMI/SDI capture must also work. Control is always over IP.
  - **Use:** personal, non-commercial project for a church, so Ultralytics (AGPL) is fine.
    Control from the PC and from tablets/phones.
  - **Venues:** distance and lighting differ every time.
  - **Mic levels:** come mainly from a Midas MR18, sometimes a Behringer X Air (mics assigned to presets/persons).
  - **Chosen extras beyond Canon:** smoother, slower moves while a camera is live on Program,
    choosing the target from the active microphone, and (asked 2026-11-04) smooth pan and tilt that
    are never jerky, with an adjustment for it: Pan/Tilt Smoothness (section 3, step 5). Where the
    cameras have no ramp of their own, TrackXen is the layer that shapes the commands, joystick moves
    included.
  - **Not wanted:** automatic lower thirds.
- See section 7 for where Canon's design and the P400 differ.

---

## 1. What Canon's app does

- It runs inside each Canon PTZ camera (camera add-on) and is operated from a browser.
- It finds people and faces in the image and moves pan/tilt/zoom to follow the chosen target.
- It can pick the target automatically, zoom to keep the target a set size, and change pan/tilt/zoom
  speed to match how the target moves.
- Working distance is about 3–30 m.
- Detection is not perfect, so an operator must always be able to correct it.
- Each camera has **5 "App Settings" slots**: complete, named tracking profiles (name up to 40
  characters) that can be switched live or by a camera preset.

## 2. Areas: the core concept

Areas are frames drawn on the video. They are stored as camera angles, so they stay fixed to the
room when the camera moves. A **View** button next to any area drives the camera to show it.

| # | Area | What it does | How many | Colour in Canon's diagram |
|---|------|--------------|----------|---------------------------|
| 1 | Shooting Range | Limits how far the camera may pan/tilt (top/bottom/left/right edges) | 1 | grey solid |
| 2 | Initial Position | Home view: where tracking starts and where the camera goes when the target is lost | 1 | orange solid |
| 3 | Priority Display Area | Region always kept in frame (whiteboard, screen); wins over following the person | 1 | blue dashed |
| 4 | Fixed Viewing Angle Area | While the target is inside, the camera holds a fixed shot; tracking resumes when they leave (podium) | up to 4, own colour each | green dashed |
| 5 | Auto Select Area | A person entering it becomes the target | 1 | red dotted |
| 6 | Auto Select Exclusion Area | Never auto-pick from here (posters, photos, screens showing people) | up to 4 | purple dotted |
| 7 | Pan/Tilt Halting Area | Dead zone around the target's framed position: no pan/tilt while the target stays inside | 1 | light blue dotted |
| 8 | Tracking Halting Area | Target entering stops tracking; nobody is detected or picked inside (e.g. seats of speakers waiting their turn) | up to 4 | magenta dotted |

Rules between areas:
- Priority Display Area and Fixed Viewing Angle Area can't both be used.
- Initial Position and Fixed Viewing Angle Areas must be inside the Shooting Range.
- Priority Display Area and Auto Select Area must be inside the Initial Position view.
  Changing the Initial Position means re-checking those two.
- Exclusion wins over Auto Select where they overlap.
- Tracking Halting wins over Fixed Viewing Angle, Auto Select and Exclusion areas.
- Shooting Range and Priority Display Area win over all Auto Framing features.
- If the image flip or digital zoom converter changes, all areas have to be set again.

## 3. All settings: the 10 wizard steps

The settings wizard walks through steps 1–10. Every step is also its own page in the Settings Menu.

### Step 1: Pan/Tilt Operation Control
- **Fix Tilt Operation** (on/off): no tilting while tracking. It also disables the top/bottom range
  edges and Sit/Stand, and stops zooming in if zooming would push the target out of frame.
- **Specify Shooting Range** (on/off), then 4 rows (top, bottom, left, right edge icons), each with
  **Capture** (the current view's edge becomes the limit) and **View** (go show that edge).

### Step 2: Initial Position
- **Capture / View** for the home angle. Help text reminds you to re-check related areas after changing it.
- **When auto tracking is on and the target is lost** (radio): *Maintain viewing angle* | *Return to initial position*.
- **Time before returning to initial position (sec)**: slider 0–30 (ex 3).
- The home view must be wide enough to detect people in it. If people aren't found after returning
  home, the view is too wide.

### Step 3: Priority Display Area / Fixed Viewing Angle Area
- **Priority Display Area** toggle: one frame. Drag inside to move it, drag its edges to resize.
- **Fixed Viewing Angle Area 1–4**: toggle and View per row. Each has its own frame colour
  (yellow, green, blue, pink in the screenshots).

### Step 4: Tracking Target Auto Select
- **Auto Select** toggle. On: a person entering the auto select area becomes the target.
  Off: the operator clicks the target.
- **Tracking Start Time (sec)**: slider 0–10 (ex 3). How long a newly detected person must be there
  before tracking starts. Only used when Auto Select is on and Registered Face Tracking is off.
- **Auto Select Area** toggle with a frame. If off, the whole view counts.
- **Restart Tracking After Manual Operation** toggle. Manual PTZ from another controller pauses
  tracking; on = tracking resumes by itself afterwards.

### Step 5: Composition
- **Display Position**: drag a person silhouette to set where the target sits in the frame. It can't
  be placed at the edges. A Priority Display Area overrides it.
- **Display Size** toggle and slider "Min"–5 (values like 0.5 and 1 seen). The silhouette's face size
  is the target face size to hold.
- **Auto Zoom** toggle: zoom continuously to keep Display Size.
- **Pan/Tilt Halting Area** toggle and frame: a dead zone centred on the silhouette's face.
  Smaller = livelier. Off = movement is driven only by Tracking Sensitivity.
  Re-check it after changing Display Size.
- **Tracking Sensitivity**: slider 1–10 (ex 7).
  - Higher is livelier, but more likely to jump to nearby people.
  - 1 is safest when switching target or moving the silhouette far.
  - If the camera itself moves (on a rail): use 1 with the smallest dead zone.
- **Beyond Canon: Pan/Tilt Smoothness**, slider 1–10 (default 5), right after Tracking Sensitivity.
  - Canon folds smoothness into Tracking Sensitivity and the halting area. Comparable trackers keep a
    separate control next to their sensitivity and dead band: Sony's Auto Framing "Speed" 1–5 (the
    acceleration level), PTZOptics' acceleration slider, Seervision's pan/tilt smoothness,
    TrackPilot's Style and Attack, OBSBOT's Lazy to Crazy.
  - Higher = softer starts, stops and speed changes; 1 = the liveliest. It works off air and on air
    (the on-air motion applies on top of it) and also ramps the app's own joystick (arrow button)
    moves. STOP ALL, tracking off, the watchdog and absolute moves are never ramped.

### Step 6: Auto Select Exclusion Area
- Areas 1–4: toggle, View and own colour each.

### Step 7: Tracking Halting Area
- Areas 1–4: toggle, View and own colour each.

### Step 8: Auto Framing
Master **Auto Framing** toggle with three sub-features.

**Multi-Person** (2–4 people moving in and out on stage): frames the main target together with
nearby secondary people. With only the main target in view, the normal Composition applies.
- **Zoom Speed**: slider 1–3 (ex 2).
- **Minimum Size When Zooming Out**: slider "Min"–3 (ex 1). Higher = zooming out stops sooner, so people don't get too small.
- **Composition Transition Speed (Single/Multiple)**: slider 1–3 (ex 3). Lower = slower change between solo and group framing.
- Secondary people drop out if the main target goes to the frame edge or is too far from them.
- Main target in a fixed area: the angle is fixed. Main target in a halting area: tracking stops.
  Secondary people inside a halting area are ignored.

**Face Direction** (one target): leave space on the side the face is pointing.
- **Adjustment Sensitivity**: slider 1–3 (ex 2).
- **Space Size**: slider 1–3 (ex 2).
- **When the target faces forward** (radio): *Maintain* | *Center*.
- Needs the face to be detected. Inactive inside fixed and halting areas.

**Sit/Stand** (one target): different size when the person is seated vs standing (larger when seated).
- **Display Size After Sitting Down**: slider (ex 4, range shown 4–5).
- **Display Size After Standing Up**: slider (ex 1, range shown 1–2).
- Turning it on forces Display Size on (using the standing value) and Auto Zoom on.
- Disabled by Fix Tilt. Unreliable when the dead zone is on or Display Position is at the top of the frame.

### Step 9: Registered Face Tracking
- **7 face slots**. Each row: drag handle (priority order), number, thumbnail, on/off toggle.
  Under the list: **Delete** and **Register** buttons.
- **Register**: pick a slot and an overlay frame appears with the instruction "face forward, centre
  your face, fill the frame". The frame turns green when the face is usable. Then **Capture** (or **Cancel**).
- **Master toggle** on means:
  - Only registered faces with their toggle on are auto-picked, also after the target is lost.
  - If several appear at once, the highest priority wins.
  - Once someone is locked, a higher-priority person appearing does not take over.
- Register in real shooting conditions (same glasses, same camera angle).
- Faces fail when they are:
  - not frontal
  - too small, large, dark or bright
  - partly covered (glasses, mask, hair, shadow) or partly out of frame
  - showing a very different expression
- At the initial position, faces should look bigger than the Display Size 1 silhouette.
- Privacy: face data stays local. Get consent before registering, and wipe it when the system is given away.

### Step 10: Camera Preset Link (shared by all 5 App Settings)
- Table: **Preset Name | Auto Tracking (On / Off / blank) | App Settings (1–5)**.
- On: after that preset is recalled, tracking starts with the chosen App Settings slot.
- Off: recalling that preset stops tracking.
- Blank: follows "Restart Tracking After Manual Operation".
- Buttons: **Initialization** (reset this table), **View** (recall the selected preset), **Finish**.

## 4. Screens

### 4.1 Tracking Execution Screen (the main operating screen)
- **Narrow icon rail** at the far left: opens the Settings Menu.
- **Left settings column**:
  - **App Settings**: 5 numbered segment buttons; the selected one is orange.
  - **Composition**:
    - Silhouette View (Single Target) toggle, which shows a draggable silhouette while tracking
      (this toggle is never saved)
    - Display Position (+)
    - Display Size toggle, slider, −/+ and value
    - Auto Zoom toggle
    - Tracking Sensitivity slider, −/+ and value
    - (beyond Canon) Pan/Tilt Smoothness slider, −/+ and value
  - **Auto Framing** toggle with indented sub-toggles Multi-Person, Face Direction, Sit/Stand.
  - **Registered Face Tracking** toggle.
  - **App Running**: **Auto Tracking** toggle (start/stop) and a **Status** badge.
  - Bottom buttons: **Initialization**, **Save**.
- **Video area**:
  - Top-left: mouse-mode dropdown (icon, mode name, ▼).
  - Top-right: **View Initial Position**.
  - Window top-right corner: **Language** and **App Info (i)** icons.
- **Overlays on the video**:
  - Cyan outline silhouette (head circle and body) at the display position.
  - Corner-bracket frames on faces: the main target's is brighter or larger, others are thinner.
  - Area frames in their colours.
  - A small icon at the top of the video while Tracking / Angle Fixed shows one target or a group.
- **Status badges**: No target selected (dark grey), Searching (purple), Tracking (green),
  Target lost (grey), Angle Fixed (green).
- **Starting**: pick App Settings, then turn Auto Tracking on. If Auto Select is off, click a person
  in Tracking Target Select mode. Stop = Auto Tracking off.
- **Changes during tracking**: they apply immediately but are only kept after **Save**. Big jumps in
  position, size or sensitivity can lose the target, so change them gradually.
- **Initialization dialog**: *Selected app settings* (keeps preset links) or *All app settings*; Cancel / OK.

### 4.2 Settings Menu (flyout from the rail)
- Entries: Tracking Execution Screen, a **Setting Wizard** button, then the 10 numbered pages
  (gear icon with the step number): Pan/Tilt Operation Control, Initial Position, Priority Display /
  Fixed Viewing Angle Area, Tracking Target Auto Select, Composition, Auto Select Exclusion Area,
  Tracking Halting Area, Auto Framing, Registered Face Tracking, Camera Preset Link.
- "<" closes it.

### 4.3 Settings wizard and individual settings pages
- **Top**: name field for the App Settings slot.
- **Left**: step icons; the current step is orange.
- **Settings panel**: title bar, controls with a short help text under each, toggles labelled "On",
  sliders with −/+ stepper and a value readout.
- **Video area**:
  - mouse-mode dropdown and View Initial Position at the top
  - **pan slider** under the video, with ◀ ▶ fine buttons
  - **tilt slider** on the right, with ▲ ▼
  - **zoom slider** at the far right: blue thumb, up = telephoto, T / W fine buttons
- **Footer**: **Previous | "5/10" | Next**; the last page shows **Finish**.
- **Saving**:
  - The wizard saves each page on Previous/Next.
  - Single pages save on Finish.
  - Finish goes back to the Tracking Execution Screen with Auto Tracking on.

### 4.4 Mouse modes (dropdown at the top-left of the video)
- **Click Centering**: click a point and the camera centres on it. Available on all settings pages.
- **Tracking Target Select**: click a person and they become the target.
- **Silhouette Manipulation**: drag the silhouette to move the target's framed position.
- **Set Area**: drag or resize area frames.
- **Silhouette Manipulation / Set Area**: both, on the Composition page. The dead-zone toggle decides
  which one a drag affects.

## 5. Behaviour and tuning knowledge (from Canon's troubleshooting)
- **Losing or swapping targets**: Display Size too big, sensitivity too low, dead zone too wide,
  Display Position at an edge, or target far from the Priority Display Area. A Tracking Halting Area
  over people you never want tracked helps.
- **Too jumpy**: Display Size big, sensitivity high or dead zone small. Lower the sensitivity, turn
  off Auto Zoom if zoom isn't needed, use a Fixed Viewing Angle Area at the podium. In Multi-Person:
  zoom speed too high, or big Display Size with a small minimum zoom-out size. (Beyond Canon: raise
  Pan/Tilt Smoothness.)
- **Sluggish**: sensitivity too low, or dead zone too large. (Beyond Canon: or Pan/Tilt Smoothness
  high, which follows sudden moves later in tight shots.)
- **Secondary people drop out (Multi-Person)**: zoom speed too low, minimum size too large,
  Display Size too large, Display Position at the top, or main target far from the priority area.
- **Status says Tracking but the camera doesn't move**:
  - priority area near the frame edge
  - people inside a fixed area
  - view at the edge of the shooting range
  - manual override is active
  - Auto Select is off and nobody has been clicked
- **Manual PTZ stops tracking**: turn on Restart Tracking After Manual Operation, or pick the target again.
- **Error messages** to copy in spirit:
  - area outside the shooting range
  - inconsistent area settings (a warning appears on the offending setting)
  - area the camera can't physically show
  - face detection disabled
  - internal error, restart
  - too many video clients
  - can't connect to the camera
  - busy
- Canon's "Lite" edition restrictions don't apply. We build the full feature set.

## 6. The user's UI reference screenshots (sent 2026-10-31)
The images themselves weren't saved, so they're described here. To keep them, save them into
`docs/ui-reference/`.

### Ref A: Canon camera web settings page ("Audio" page)
- **Theme**: very dark grey background, white text, orange accent, green primary button.
- **Far-left rail** (~40 px): ">" expand chevron, a camera-control icon, a gear icon. The active icon
  has an orange bar on its left edge.
- **Navigation column** (~190 px, slightly lighter): group headings Video and Audio, Server,
  Communication, Security, System, Maintenance. The open group shows indented sub-items (HDMI/SDI,
  IP Streaming Video, Audio, Movement Range); the active one has an orange left bar.
- **Content**:
  - Big bold page title at the top-left.
  - Top-right: **Apply** (solid green) and **Clear** (grey) buttons.
  - Section headings in small bold ("General Audio", "IP Audio Streaming"), each followed by a thin
    divider line to the right.
- **Form rows**: label on the left, a wide control on the right.
  - Dark grey dropdowns with a chevron. The open dropdown is a light grey menu with a ✓ on the
    current value.
  - Black number fields; the allowed range is shown small next to the label ("1 - 100").
  - Live level meters made of ~10 segments.
  - A small green dot to the right of a changed field (looks like an "edited, not applied" marker).

### Ref B: Canon Remote Camera Control Application (multi-camera control app)
- **Left panel "Camera List"** (☰ menu, "<" to collapse): vertical camera cards. Each card has the
  camera name, a live thumbnail and an orange left border when in use; under the thumbnail, a swap
  (⇄) button and a settings ⚙ button.
- **Centre**: 2×2 grid of camera slots 1–4.
  - Slot header: number badge (orange when in use, grey when empty), camera name, and a dropdown on
    the right showing "OFF".
  - Under the header: output type ("HDMI/3G-SDI") on the left, format ("59.94P 1920x1080") on the right.
  - Live video with position markers: a white bar on the left edge = tilt, a white bar at the bottom = pan,
    a blue bar on the right edge = zoom.
  - Under the video: exposure readout (white balance, ND, iris, gain, shutter) and an (i) button.
  - Empty slots are greyed out.
- **Right panel** (">" to collapse): tabs **Basic | Details | Add-on**, active tab underlined in orange.
  A thin icon rail on the far right jumps to sections: PTZ, Preset, Image, WB, and one more.
  Canon puts its tracking add-on controls under the Add-on tab.
  - **PTZ/Focus**:
    - AF toggle (orange) and One Shot AF button
    - Near…Far row of 6 step buttons (<<< << < > >> >>>)
    - Pan/Tilt Speed and Zoom Speed sliders
    - small Pan/Tilt (white) and Zoom (blue) position indicators
    - 3×3 direction pad (8 arrows, empty centre)
    - T / W buttons
  - **Preset**:
    - Speed Specification toggle
    - segmented control: Time (sec.) | Speed Level
    - slider 2.0–99.0 (shows 47.5) with −/+
    - Freeze Image During Movement toggle
    - grid of numbered preset buttons

### Visual tokens (eyeballed from the screenshots, adjust when building)
- **Backgrounds**: app ~#1c1c1c, panels ~#2a2a2a, controls ~#3a3a3a, dividers ~#444.
- **Text**: ~#e8e8e8, secondary ~#a0a0a0.
- **Accent orange** ~#d9730d: toggles on, active nav, selected slot, badges.
- **Primary green** ~#0a8a2e: Apply, "Tracking" status.
- **Status purple** for Searching.
- **Blue** for zoom indicators.

## 7. Where Canon's design and the P400 differ (things to check, not a plan)
- **Where it runs**: Canon's app runs inside each camera and uses the camera's own face/body detection.
  Ours runs on the PC for all 4 cameras, using NDI video and GPU detection.
- **Position readback**: Canon stores areas as camera angles. We need the P400's pan/tilt/zoom
  position readback (VISCA inquiries) to do the same; to be checked on the real cameras.
- **Manual operation**: Canon notices when another controller moves the camera. We would have to
  notice it ourselves, for example by polling the camera's position.
- **Preset recalls**: Camera Preset Link needs to know when a preset is recalled. That's easy when
  the recall goes through our app; it's unclear when vMix or a hardware controller recalls it.
- **Faces**: Canon keeps registered faces in the camera. We would keep face data locally on the PC.

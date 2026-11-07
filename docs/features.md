# TrackXen features

TrackXen follows speakers with up to four PTZ cameras. It runs on one Windows PC: it reads each
camera's picture (NDI, a capture card or the camera's own network stream), finds the people in it on
the GPU, and steers the cameras over VISCA, with their exact position from FreeD where the camera sends
it. It is operated from a browser, on the PC or on a tablet or phone.

This page describes what TrackXen can do and every setting. The [user guide](user-guide.md) says how to
use it day to day.

- [App Settings](#app-settings)
- [Areas](#areas)
- [The settings pages](#the-settings-pages)
- [Beyond the camera picture](#beyond-the-camera-picture)
- [Tuning tips](#tuning-tips)
- [How it works with the cameras](#how-it-works-with-the-cameras)

## App Settings

Each camera has **5 App Settings**: complete, named tracking setups (a name of up to 40 characters,
for example "Sermon", "Worship", "Panel"). They switch live from the tracking panel, from Bitfocus
Companion, or by recalling a camera preset (Camera Preset Link). Changes work at once; **Save** keeps
them. **Initialization** resets the selected App Settings, or all of them and the preset links.

## Areas

Areas are frames drawn on the camera picture. They are kept as camera angles, so they stay fixed to
the room when the camera moves. A **View** button next to an area turns the camera to show it.

| Area | What it does | How many | On the picture |
|------|--------------|----------|----------------|
| Shooting Range | How far the camera may pan and tilt (top, bottom, left and right edges) | 1 | grey, solid |
| Initial Position | The home view: where tracking starts, and where the camera goes when the target is lost | 1 | orange, solid |
| Priority Display Area | Always kept in the picture (a screen, a whiteboard); wins over following the person | 1 | blue, dashed |
| Fixed Viewing Angle Area | While the target is inside, the camera holds one shot (a podium); tracking goes on when they leave | up to 4 | yellow, green, blue, pink, dashed |
| Auto Select Area | A person in it becomes the target | 1 | red, dotted |
| Auto Select Exclusion Area | Never picked from here (posters, photos, screens showing people) | up to 4 | purple, dotted |
| Pan/Tilt Halting Area | A quiet zone around the target's place in the picture: no pan or tilt while their face stays inside | 1 | light blue, dotted |
| Tracking Halting Area | A target walking in is let go, and nobody inside is picked (the seats of people waiting their turn) | up to 4 | magenta, dotted |

Rules between areas (the settings pages warn when one is broken):
- A Priority Display Area and Fixed Viewing Angle Areas can't both be used.
- The Initial Position and Fixed Viewing Angle Areas must be inside the Shooting Range.
- The Priority Display Area and the Auto Select Area must be inside the Initial Position's view. After
  changing the Initial Position, check those two again.
- Exclusion wins over Auto Select where they overlap.
- A Tracking Halting Area wins over Fixed Viewing Angle, Auto Select and Exclusion areas.
- The Shooting Range and the Priority Display Area win over Auto Framing.

## The settings pages

The **Setting Wizard** goes through the ten pages with Previous and Next; **Settings pages** opens any
one of them. Every page shows the camera picture with pan, tilt and zoom sliders around it. The pages
are in four groups. The main ones (1, 2, 3 and 6) are enough for most venues: Next goes through them
only, unless "Optional steps too" is ticked. The list marks a page in use with ✓, and a main step still
to do with !.

### Picture area

**1. Pan/Tilt Operation Control**
- **Fix Tilt Operation**: no tilting while tracking. It also switches off the top and bottom range
  edges and Sit/Stand, and stops zooming in when that would push the target out of the picture.
- **Specify Shooting Range**, with **Capture** (the edge of the current picture becomes the limit) and
  **View** for each of the four edges.

**2. Initial Position**
- **Capture** and **View** for the home view. It must be wide enough to find people in it.
- **When auto tracking is on and the target is lost**: *Maintain viewing angle* or *Return to initial
  position*, after **Time before returning** (0–30 s).

### Choosing the target

**3. Tracking Target Auto Select**
- **Auto Select**: on, a person in the Auto Select Area becomes the target after **Tracking Start
  Time** (0–10 s); off, the operator clicks the target in the picture.
- **Auto Select Area**: off, the whole picture counts.
- **Restart Tracking After Manual Operation**: moving the camera by hand (vMix, a joystick, the camera's
  web page, TrackXen's PTZ buttons) stops tracking; on, tracking starts again by itself once the camera
  is still.

**4. Auto Select Exclusion Areas** (optional): up to 4.

**5. Tracking Halting Areas** (optional): up to 4.

### Framing and movement

**6. Composition**
- *Framing*
  - **Display Position**: drag the silhouette to where the target's face should be. Not at the
    picture's edges. A Priority Display Area overrides it.
  - **Display Size**: zoom so the person is this tall in the picture when a target is locked;
    **Auto Zoom** keeps that size while tracking.
- *Movement*
  - **Pan/Tilt Halting Area** and its size: smaller is livelier. Off, movement follows the sensitivity
    alone (a small quiet zone stays so the tilt doesn't follow a walking head's bob).
  - **Tracking Sensitivity** 1–10: higher follows more nimbly but may jump to people nearby; 1 is
    safest when changing target or moving the silhouette far.
  - **Pan/Tilt Smoothness** 1–10 (default 5): higher starts, stops and changes speed more softly; 1 is
    the liveliest. It also ramps TrackXen's own PTZ buttons, Companion and API moves. STOP ALL,
    switching tracking off, the "no video" stop and closing a camera always stop at once.
- *On-Air Motion* (while the camera is live on Program in vMix, or switched On Air by hand): a share of
  the top speed, softer starts and stops, a bigger halting area, slow or no zoom, and whether a new
  target waits until the camera is off Program. The change is blended over a second.

**7. Priority Display Area / Fixed Viewing Angle Areas** (optional)
- **Priority Display Area**: one frame, moved and resized on the picture.
- **Fixed Viewing Angle Areas 1–4**: each with its own shot (set with the sliders and **Capture**).

**8. Auto Framing** (optional): a master switch with three parts.
- **Multi-Person** (2–4 people coming and going): frames the target with the people near them.
  **Zoom Speed** 1–3, **Minimum Size When Zooming Out**, **Composition Transition Speed** 1–3 (lower:
  slower change between single and group). People drop out when the target goes to the picture's edge
  or far from them.
- **Face Direction** (one target): leaves space on the side the face points to. **Adjustment
  Sensitivity** 1–3, **Space Size** 1–3, and when the target faces forward: *Maintain* or *Center*.
- **Sit/Stand** (one target): closer while the person sits. Its own sitting and standing sizes;
  turning it on switches Display Size and Auto Zoom on. Not with Fix Tilt.

### Extras

**9. Registered Face Tracking** (optional)
- Up to 7 registered faces per camera, in priority order, each on or off. A face registered on one
  camera can be added to the others.
- **Register**: the face of a person in the centred frame, or **Click a person…** anywhere in the
  picture (the frame follows their head). The frame turns green when the face is usable; then
  **Capture**.
- On: only registered faces that are switched on are picked, also after the target is lost; the
  highest in the list wins when several appear; once someone is tracked, a higher one appearing doesn't
  take over.
- Faces fail when turned away, too small, too dark or bright, partly covered or out of the picture, or
  with a very different expression. Register in the real conditions (same glasses, same angle).
- Face data stays on the TrackXen PC. Ask people before registering them; **Delete all face data**
  (Settings → System) when it is no longer needed.

**10. Camera Preset Link** (optional; shared by all 5 App Settings)
- Per camera preset: **Auto Tracking** On (start tracking with the chosen App Settings once the camera
  is there), Off (stop tracking), or blank (like any manual operation).

## Beyond the camera picture

- **On air from vMix:** TrackXen reads vMix's tally. A camera on Program moves calmly (On-Air Motion),
  and a red border and **LIVE** show it. Without vMix, the **On Air** switch does the same.
- **Audio follow:** with a Midas MR18 or Behringer X Air mixer, a microphone that starts speaking can
  send a free camera to that person (a preset, or a registered face). A camera the operator just used is
  left alone, and STOP ALL switches audio follow off.
- **Bitfocus Companion and the HTTP API:** STOP ALL, Auto Tracking, App Settings, presets, the target,
  On Air and Audio follow as Stream Deck buttons (user guide, section 7).
- **Statistics and problem lights:** detection time, frame rates, GPU and CPU load, camera replies, and
  a light for anything that needs attention; daily logs and graphs.
- **A PIN** for phones and tablets, and **show files** that keep a whole venue's setup.
- **Updates from GitHub:** Settings → System → Updates installs the newest build.

## Tuning tips

- **The target is lost or swapped:** Display Size too big, sensitivity too low, halting area too
  wide, Display Position at an edge, or the target far from the Priority Display Area. A Tracking
  Halting Area over people who should never be tracked helps.
- **Too jumpy:** raise Pan/Tilt Smoothness, lower the sensitivity, switch Auto Zoom off if zooming
  isn't needed, or use a Fixed Viewing Angle Area at the podium. In Multi-Person: a lower zoom speed, or
  a larger minimum zoom-out size.
- **Sluggish:** sensitivity too low, halting area too large, or Pan/Tilt Smoothness high (sudden moves
  are followed later, mostly in tight shots).
- **People drop out of a group (Multi-Person):** zoom speed too low, minimum size too large, Display
  Size too large, Display Position at the top, or the target far from the priority area.
- **Status says Tracking but the camera doesn't move:** the priority area is near the picture's edge,
  the person is inside a fixed area, the view is at the edge of the shooting range, someone is moving
  the camera by hand, or Auto Select is off and nobody has been clicked.
- **Moving the camera by hand stops tracking:** switch on Restart Tracking After Manual Operation, or
  pick the target again.

## How it works with the cameras

- **Where it runs:** on one PC for all four cameras, with GPU detection on the cameras' pictures.
- **Where the camera points:** FreeD from the camera (BirdDog P400), or position inquiries over VISCA
  (PTZOptics, or a camera without FreeD). The areas, the composition and the smoothing all build on it.
- **Camera types:** BirdDog P400, PTZOptics G2 20X, PTZOptics G2 12X and "Other VISCA camera". A type fills
  in the camera's control, speeds, video delay and lens. The G2 types carry a measured zoom table: the field
  of view at each zoom position, which the pixel-to-angle conversion and the framing use. Without a table a
  lens curves evenly between its widest and narrowest view, which can be 25-37 % off in the middle.
- **Moves by someone else:** TrackXen notices when another controller moves the camera and treats it
  as a manual operation.
- **Presets recalled elsewhere:** the Camera Preset Link acts on presets recalled from TrackXen,
  Companion or the API.
- **Smooth moves:** the cameras have no setting for how softly they start and stop, so TrackXen shapes
  every speed command itself (Pan/Tilt Smoothness).

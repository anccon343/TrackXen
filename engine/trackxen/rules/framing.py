"""Auto Framing: what to frame this frame, beyond "the target's head at
the display position".

- **Multi-Person:** people near the main target are framed with them (2-4 people); the camera
  zooms out to fit the group, but never so far that the main person gets smaller than the
  minimum size. Joining and leaving blend smoothly at the transition speed.
- **Face Direction** (one person): leave space on the side the face points to.
- **Sit/Stand** (one person): a bigger display size when the person sits, a smaller one standing.

Everything works in room angles (degrees), so the camera's own movement doesn't disturb it.
Shooting range, priority display area, fixed and halting areas still win: the session applies
them around this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from trackxen.control.camera import Pose
from trackxen.control.controller import FACE_MIN_CONF, cut_at_bottom, head_point
from trackxen.control.lens import Lens
from trackxen.core.tracking_settings import AppSettings
from trackxen.rules.areas import Placed

# COCO keypoints
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_KNEE, R_KNEE = 5, 6, 11, 12, 13, 14
SEEN = 0.3  # keypoint confidence that counts as visible

MIN_SIZE = [0.15, 0.25, 0.35, 0.45]  # Minimum Size When Zooming Out 0..3: main person's height share
TRANSITION_S = [3.0, 1.5, 0.7]  # Composition Transition Speed 1..3: seconds single <-> group
ZOOM_STEP = [2, 3, 5]  # Zoom Speed 1..3: top VISCA zoom step while framing a group
FACE_TIME = [2.0, 1.0, 0.5]  # Face Direction Adjustment Sensitivity 1..3: smoothing time constant
FACE_SPACE = [0.06, 0.10, 0.14]  # Space Size 1..3: display position shift, share of the width
GROUP_MARGIN = 1.3  # group width x this = the field of view framing it
JOIN_S, LEAVE_S = 0.5, 1.0  # a person must qualify this long to join, fail this long to leave
SAME_PLACE = 2.5  # degrees: a new track where a member just vanished is that member (new ID)
EDGE_ROOM = 0.85  # the main target must stay inside this share of the picture (and of the widest view)
MAX_GROUP = 4
FACE_HEIGHT_TAU = 1.0  # s: low-pass on where the face sits in the person's box (the point used when it is lost)
SIT_RATIO, STAND_RATIO, POSTURE_S = 0.45, 0.7, 0.7  # knee drop / torso; seconds before a change counts


@dataclass
class Shot:
    """What the controller frames: the target's head (`x`, `y`, pixels of this frame) moved by
    `offset` (degrees: towards a group's middle) at `anchor` (0..1 of the picture), zooming to
    `want_hfov` degrees or else to `target_height` (share of the picture height), with zoom
    steps up to `zoom_step`. The head stays what the motion filter follows: blending towards a
    group is a change of aim, not the person moving (which would make the camera overshoot)."""

    x: float
    y: float
    anchor: tuple[float, float]
    offset: tuple[float, float] = (0.0, 0.0)
    want_hfov: float | None = None
    target_height: float | None = None
    zoom_step: int | None = None
    group: list[int] = field(default_factory=list)  # who is framed with the main target
    facing: str = ""  # "left", "right", "forward"; "" = off
    posture: str = ""  # "sitting", "standing"; "" = off or not known yet


def facing_yaw(keypoints: list[list[float]]) -> float | None:
    """Which way the face points across the picture: -1 = to picture-left, +1 = right, None = unknown."""
    kp = keypoints
    if kp[NOSE][2] < SEEN:
        return None
    nose = kp[NOSE][0]
    ears = [kp[i][0] for i in (L_EAR, R_EAR) if kp[i][2] >= SEEN]
    eyes = [kp[i][0] for i in (L_EYE, R_EYE) if kp[i][2] >= SEEN]
    if len(ears) == 2 and abs(ears[0] - ears[1]) > 2:
        return max(-1.0, min(1.0, (nose - (ears[0] + ears[1]) / 2) / (abs(ears[0] - ears[1]) / 2)))
    if len(ears) == 1:  # profile: the nose is on the side the face points to
        return -1.0 if nose < ears[0] else 1.0
    if len(eyes) == 2 and abs(eyes[0] - eyes[1]) > 2:
        return max(-1.0, min(1.0, (nose - (eyes[0] + eyes[1]) / 2) / abs(eyes[0] - eyes[1])))
    return None


def posture_ratio(keypoints: list[list[float]]) -> float | None:
    """How far the knees are below the hips, against the torso length: ~1 standing, ~0-0.4 sitting."""
    ratios = []
    for shoulder, hip, knee in ((L_SHOULDER, L_HIP, L_KNEE), (R_SHOULDER, R_HIP, R_KNEE)):
        s, h, k = keypoints[shoulder], keypoints[hip], keypoints[knee]
        if min(s[2], h[2], k[2]) >= SEEN and h[1] - s[1] > 1:
            ratios.append((k[1] - h[1]) / (h[1] - s[1]))
    return sum(ratios) / len(ratios) if ratios else None


class Framer:
    """Keeps what lasts between frames: who is in the group, the blend, face and posture."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.blend = 0.0  # 0 = single composition, 1 = group composition
        self.members: dict[int, float] = {}  # framed with the main target -> since when
        self.candidates: dict[int, float] = {}  # qualifying, waiting to join -> since when
        self.failing: dict[int, float] = {}  # members that stopped qualifying -> since when
        self.member_pan: dict[int, float] = {}  # where each member was last seen
        self.home_hfov: float | None = None  # the view before the group formed, to return to
        self.group_view: tuple[float, float, float] | None = None  # pan, tilt, hfov of the last group
        self.yaw = 0.0
        self.facing = "forward"
        self.shift = 0.0  # display position shift now (share of the width)
        self.posture = ""
        self.posture_since: float | None = None
        self.last_t: float | None = None
        self.face_height: float | None = None  # where the face has been in the box, share of its height

    def _head(self, box: list[float], keypoints: list[list[float]], dt: float) -> tuple[float, float]:
        """head_point, without its jump when the face is lost (looking down at notes, turned away): the box-top
        fallback goes where the face has been in the box (low-passed), not to a fixed 0.1 of it. Measured: tilt
        starts while walking at an 8-degree view 8 -> 3.4 a minute, standing with the Halting Area off 5.7 -> 1."""
        x, y = head_point(box, keypoints)
        top, height = box[1], max(1.0, box[3] - box[1])
        if any(c >= FACE_MIN_CONF for _, _, c in keypoints[:5]):  # the face is seen
            share = min(0.5, max(0.0, (y - top) / height))
            k = 1.0 if self.face_height is None else min(1.0, dt / FACE_HEIGHT_TAU)
            self.face_height = share if self.face_height is None else self.face_height + (share - self.face_height) * k
            return x, y
        return x, (y if self.face_height is None else top + self.face_height * height)

    def shot(self, slot: AppSettings, main: Placed, people: list[Placed], zones: dict[int, str],
             msg: dict, pose: Pose, lens: Lens, t: float) -> Shot:
        """The framing for this frame; `main` is the target, `people` everyone placed in it."""
        dt = 0.0 if self.last_t is None else max(0.0, min(0.5, t - self.last_t))
        self.last_t = t
        af, comp = slot.auto_framing, slot.composition
        hx, hy = self._head(main.track["box"], main.track["keypoints"], dt)
        anchor_x, anchor_y = comp.anchor_x, comp.anchor_y
        target_height, facing, posture = None, "", ""

        if af.enabled and af.sit_stand.enabled and not slot.fix_tilt:
            posture = self._posture(main.track["keypoints"], t)
            if posture:
                target_height = af.sit_stand.sit_size if posture == "sitting" else af.sit_stand.stand_size
        if af.enabled and af.face_direction.enabled:
            facing = self._facing(main.track["keypoints"], af.face_direction, dt)
            anchor_x = min(0.85, max(0.15, anchor_x + self.shift))
        else:
            self.shift = 0.0

        if af.enabled and af.multi_person.enabled:
            group = self._group(slot, main, people, zones, pose, lens, t)  # members seen in this frame
            goal = 1.0 if self.members else 0.0
            step = dt / TRANSITION_S[af.multi_person.transition_speed - 1]
            self.blend = min(goal, self.blend + step) if goal > self.blend else max(goal, self.blend - step)
        else:
            self.members, self.candidates, self.failing = {}, {}, {}
            group, self.blend = [], 0.0

        cut = cut_at_bottom(main.track["box"], msg["height"])
        if self.blend <= 0.0:  # one person: the normal composition
            want = None
            if self.home_hfov is not None:  # a group just ended: back to the single composition's view
                # (Sit/Stand and Auto Zoom keep the size themselves; else the display size once, or the view
                # from before the group)
                back = (None if target_height or (comp.display_size and comp.auto_zoom)
                        else min(lens.hfov_wide, max(lens.hfov_tele, self._single_hfov(comp, None, main, pose, lens, cut))))
                if back is None or (abs(math.log(lens.hfov(pose.zoom) / back))
                                    <= math.log(1 + comp.zoom_dead_band) + 1e-6):  # where the controller stops
                    self.home_hfov = None
                else:
                    want = back
            return Shot(hx, hy, (anchor_x, anchor_y), want_hfov=want, target_height=target_height,
                        facing=facing, posture=posture)

        # Blend from the single composition (main head at the display position) to the group's
        if self.home_hfov is None:
            self.home_hfov = lens.hfov(pose.zoom)
        if group:
            self.group_view = self._view_of([main, *group])
        single_hfov = self._single_hfov(comp, target_height, main, pose, lens, cut)
        g_pan, g_tilt, g_hfov = self.group_view or (main.pan, main.tilt, single_hfov)
        b = self.blend
        offset = ((g_pan - main.pan) * b, (g_tilt - main.tilt) * b)
        want = math.exp(math.log(single_hfov) + (math.log(max(g_hfov, single_hfov)) - math.log(single_hfov)) * b)
        ax = anchor_x + (0.5 - anchor_x) * b  # a group is centred across the picture
        return Shot(hx, hy, (ax, anchor_y), offset, want_hfov=want, zoom_step=ZOOM_STEP[af.multi_person.zoom_speed - 1],
                    group=sorted(self.members))

    # --- Multi-Person ------------------------------------------------------------------

    def _group(self, slot: AppSettings, main: Placed, people: list[Placed], zones: dict[int, str],
               pose: Pose, lens: Lens, t: float) -> list[Placed]:
        """People framed with the main target: nearest first, while the view they need keeps the main
        person at least the minimum size and the lens can still show it. Nobody joins while the main
        target is near the edge of the picture (sub-targets drop out then). Joining and
        leaving have a little patience (no flicker)."""
        min_share = MIN_SIZE[slot.auto_framing.multi_person.min_size]
        widest = min(lens.hfov_for_vfov(main.height / min_share), lens.hfov_wide * EDGE_ROOM)
        near_edge = abs(main.pan - pose.pan) > lens.hfov(pose.zoom) / 2 * EDGE_ROOM
        others = [] if near_edge else sorted((p for p in people if p.id != main.id and not zones.get(p.id)),
                                             key=lambda p: abs(p.pan - main.pan))
        fits, left, right = [], main.left, main.right
        for p in others[:MAX_GROUP - 1]:
            if (max(right, p.right) - min(left, p.left)) * GROUP_MARGIN > widest:
                break
            fits.append(p.id)
            left, right = min(left, p.left), max(right, p.right)
        # The tracker gives a person a new ID when it loses them for a moment (often while the
        # camera swings): a new track right where a member just vanished keeps the membership
        by_id = {p.id: p for p in people}
        vanished = [m for m in self.members if m not in by_id]
        for pid in fits:
            if pid in self.members:
                continue
            old = next((m for m in vanished if abs(self.member_pan.get(m, 1e9) - by_id[pid].pan) < SAME_PLACE), None)
            if old is not None:
                self.members[pid] = self.members.pop(old)
                self.failing.pop(old, None)
                self.member_pan.pop(old, None)
                vanished.remove(old)
        for pid in fits:
            self.failing.pop(pid, None)
            if pid not in self.members:
                self.candidates.setdefault(pid, t)
                if t - self.candidates[pid] >= JOIN_S:
                    self.members[pid] = t
                    del self.candidates[pid]
        for pid in [p for p in self.candidates if p not in fits]:
            del self.candidates[pid]
        for pid in [p for p in self.members if p not in fits]:
            self.failing.setdefault(pid, t)
            if t - self.failing[pid] >= LEAVE_S:
                del self.members[pid], self.failing[pid]
                self.member_pan.pop(pid, None)
        for pid in self.members:
            if pid in by_id:
                self.member_pan[pid] = by_id[pid].pan
        return [by_id[pid] for pid in self.members if pid in by_id]

    @staticmethod
    def _view_of(group: list[Placed]) -> tuple[float, float, float]:
        """Pan and tilt (heads on average at the display position) and the width framing everyone."""
        left, right = min(p.left for p in group), max(p.right for p in group)
        return (left + right) / 2, sum(p.tilt for p in group) / len(group), (right - left) * GROUP_MARGIN

    def _single_hfov(self, comp, target_height: float | None, main: Placed, pose: Pose, lens: Lens,
                     cut: bool = False) -> float:
        """The view the single composition wants: the display size, or the view from before the group.
        With the main person cut off by the picture's bottom edge (bigger than the box), never narrower
        than the view now."""
        size = target_height or (comp.target_height if comp.display_size else None)
        if size and main.height > 0:
            hfov = lens.hfov_for_vfov(main.height / size)
            return max(hfov, lens.hfov(pose.zoom)) if cut else hfov
        return self.home_hfov or lens.hfov(pose.zoom)

    # --- Face Direction ----------------------------------------------------------------

    def _facing(self, keypoints: list[list[float]], fd, dt: float) -> str:
        yaw = facing_yaw(keypoints)
        if yaw is not None and dt > 0:
            self.yaw += (yaw - self.yaw) * (1.0 - math.exp(-dt / FACE_TIME[fd.sensitivity - 1]))
        if self.facing != "left" and self.yaw < -0.45:
            self.facing = "left"
        elif self.facing != "right" and self.yaw > 0.45:
            self.facing = "right"
        elif self.facing != "forward" and abs(self.yaw) < 0.2:
            self.facing = "forward"
        space = FACE_SPACE[fd.space - 1]
        # space in front of the face: someone looking left is placed right of the display position
        goal = {"left": space, "right": -space}.get(self.facing)
        if goal is None:
            goal = 0.0 if fd.forward == "center" else self.shift
        rate = space  # the whole shift in about a second
        self.shift += max(-rate * dt, min(rate * dt, goal - self.shift))
        return self.facing

    # --- Sit/Stand ------------------------------------------------------------------------

    def _posture(self, keypoints: list[list[float]], t: float) -> str:
        ratio = posture_ratio(keypoints)
        seen = "" if ratio is None else "sitting" if ratio < SIT_RATIO else "standing" if ratio > STAND_RATIO else ""
        if not seen or seen == self.posture:
            self.posture_since = None
        elif not self.posture:
            self.posture = seen  # the first look decides at once
        else:
            if self.posture_since is None:
                self.posture_since = t
            if t - self.posture_since >= POSTURE_S:
                self.posture, self.posture_since = seen, None
        return self.posture

<script lang="ts">
  import { live, type AreaInView, type AreaKind, type CameraStatus } from "../lib/live.svelte";
  import { areaStyle, EDITABLE } from "../lib/areas";
  import { faces } from "../lib/faces.svelte";
  import { slots } from "../lib/slots.svelte";
  import { ui } from "../lib/ui.svelte";

  // Live picture with overlays: the App Settings' areas in their colours, the
  // framing point and Pan/Tilt Halting Area, the silhouette, people (the target with orange
  // corner brackets). What a click or drag does follows the mouse mode (ui.mouseMode):
  // pick a target, centre the camera there, move the silhouette, or move/resize areas.
  let { camera, interactive = true, overlays = true, editable = EDITABLE, shown = null }: {
    camera: CameraStatus; interactive?: boolean; overlays?: boolean;
    editable?: AreaKind[]; shown?: AreaKind[] | null;
  } = $props();

  let canvas: HTMLCanvasElement;
  type Drag =
    | { what: "area"; area: AreaInView; mode: "move" | "l" | "r" | "t" | "b" | "lt" | "rt" | "lb" | "rb"; x0: number; y0: number; rect: [number, number, number, number] }
    | { what: "silhouette"; x: number; y: number }
    | { what: "click"; x: number; y: number };
  let drag: Drag | null = null;
  let override: { kind: AreaKind; index: number; rect: [number, number, number, number] } | null = null;
  let silhouetteAt: { x: number; y: number } | null = null;
  let lastSent = 0;

  // Redrawn only when something it shows changed: a new picture or status, an edit, the size.
  $effect(() => {
    let raf = 0;
    let drawn: unknown[] = [];
    const loop = () => {
      const now = [live.previews[camera.id], camera, override, silhouetteAt, ui.mouseMode, ui.silhouette, faces.guide,
                   faces.list, interactive, overlays, editable, shown, canvas?.clientWidth, canvas?.clientHeight,
                   window.devicePixelRatio];
      if (now.some((v, i) => v !== drawn[i])) {
        drawn = now;
        draw();
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  });

  function corners(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, len: number) {
    ctx.beginPath();
    for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
      ctx.moveTo(cx + dx * len, cy);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx, cy + dy * len);
    }
    ctx.stroke();
  }

  function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, bg: string, dpr: number) {
    ctx.font = `${11 * dpr}px system-ui`;
    const tw = ctx.measureText(text).width + 8 * dpr;
    ctx.fillStyle = bg;
    ctx.fillRect(x, y - 16 * dpr, tw, 16 * dpr);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + 4 * dpr, y - 4 * dpr);
  }

  function rectOf(a: AreaInView): [number, number, number, number] {
    return override && override.kind === a.kind && override.index === a.index ? override.rect : a.rect;
  }

  function anchor(): { x: number; y: number } {
    return silhouetteAt ?? { x: camera.framing.anchor_x, y: camera.framing.anchor_y };
  }

  function drawSilhouette(ctx: CanvasRenderingContext2D, w: number, h: number, dpr: number) {
    const { x, y } = anchor();
    const person = camera.framing.target_height * h; // person height in canvas pixels
    const r = person * 0.065, cx = x * w, cy = y * h;
    ctx.strokeStyle = "rgba(91, 192, 235, 0.95)";
    ctx.lineWidth = 2 * dpr;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    const neck = cy + r * 1.15, shoulder = neck + person * 0.04, half = person * 0.13, bottom = Math.min(h, cy + person * 0.85);
    ctx.moveTo(cx - r * 0.45, neck);
    ctx.lineTo(cx - r * 0.45, neck + person * 0.02);
    ctx.quadraticCurveTo(cx - half, shoulder, cx - half, shoulder + person * 0.06);
    ctx.lineTo(cx - half * 0.8, bottom);
    ctx.moveTo(cx + r * 0.45, neck);
    ctx.lineTo(cx + r * 0.45, neck + person * 0.02);
    ctx.quadraticCurveTo(cx + half, shoulder, cx + half, shoulder + person * 0.06);
    ctx.lineTo(cx + half * 0.8, bottom);
    ctx.stroke();
  }

  function draw() {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    const img = live.previews[camera.id];
    if (img) {
      ctx.drawImage(img, 0, 0, w, h);
    } else {
      ctx.fillStyle = "#777";
      ctx.font = `${14 * dpr}px system-ui`;
      ctx.textAlign = "center";
      ctx.fillText(camera.state === "no_video" ? "No video" : "Waiting for video…", w / 2, h / 2);
      ctx.textAlign = "start";
    }
    if (!overlays || !camera.frame.width) return;
    const sx = w / camera.frame.width, sy = h / camera.frame.height;
    const editing = ui.mouseMode === "area";

    // Areas of the active App Settings
    for (const a of camera.areas ?? []) {
      if (shown && !shown.includes(a.kind)) continue;
      const style = areaStyle(a.kind, a.index);
      const [x1, y1, x2, y2] = rectOf(a);
      const canEdit = editing && interactive && editable.includes(a.kind);
      ctx.strokeStyle = style.colour;
      ctx.lineWidth = (canEdit ? 2.5 : 1.8) * dpr;
      ctx.setLineDash(style.dash.map((d) => d * dpr));
      ctx.strokeRect(x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h);
      ctx.setLineDash([]);
      if (canEdit) {
        ctx.fillStyle = style.colour;
        for (const [hx, hy] of [[x1, y1], [x2, y1], [x1, y2], [x2, y2]]) ctx.fillRect(hx * w - 4 * dpr, hy * h - 4 * dpr, 8 * dpr, 8 * dpr);
      }
      label(ctx, style.label, Math.max(0, x1 * w), Math.max(16 * dpr, y1 * h), "rgba(0,0,0,0.6)", dpr);
    }

    // Framing point and Pan/Tilt Halting Area (the dead zone around it)
    const f = camera.framing, at = anchor();
    ctx.lineWidth = 1.5 * dpr;
    ctx.strokeStyle = "rgba(91, 192, 235, 0.75)";
    if (camera.halting_area) {
      ctx.setLineDash([2 * dpr, 4 * dpr]);
      ctx.strokeRect((at.x - f.dead_zone_x) * w, (at.y - f.dead_zone_y) * h, 2 * f.dead_zone_x * w, 2 * f.dead_zone_y * h);
      ctx.setLineDash([]);
    }
    ctx.beginPath();
    ctx.moveTo(at.x * w - 10 * dpr, at.y * h);
    ctx.lineTo(at.x * w + 10 * dpr, at.y * h);
    ctx.moveTo(at.x * w, at.y * h - 10 * dpr);
    ctx.lineTo(at.x * w, at.y * h + 10 * dpr);
    ctx.stroke();
    if (ui.silhouette || ui.mouseMode === "silhouette") drawSilhouette(ctx, w, h, dpr);

    // People
    for (const t of camera.tracks) {
      const [x1, y1, x2, y2] = t.box;
      const isTarget = t.id === camera.target;
      const zone = camera.zones?.[String(t.id)];
      const bx = x1 * sx, by = y1 * sy, bw = (x2 - x1) * sx, bh = (y2 - y1) * sy;
      if (zone) {
        ctx.strokeStyle = zone === "halting" ? "rgba(224, 64, 251, 0.8)" : "rgba(165, 105, 189, 0.8)";
        ctx.lineWidth = 1.2 * dpr;
        ctx.setLineDash([2 * dpr, 3 * dpr]);
        ctx.strokeRect(bx, by, bw, bh);
        ctx.setLineDash([]);
        label(ctx, zone === "halting" ? "halting area" : "excluded", bx, by, "rgba(0,0,0,0.55)", dpr);
        continue;
      }
      const inGroup = camera.group?.includes(t.id);
      ctx.strokeStyle = isTarget || inGroup ? "#d9730d" : t.missed ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.7)";
      ctx.lineWidth = (isTarget ? 3 : 1.5) * dpr;
      if (isTarget || inGroup) corners(ctx, bx, by, bw, bh, Math.min(bw, bh) * (isTarget ? 0.25 : 0.15));
      else ctx.strokeRect(bx, by, bw, bh);
      const wait = camera.auto_select_wait?.[String(t.id)];
      const extra = isTarget ? [camera.facing && camera.facing !== "forward" ? `facing ${camera.facing}` : "", camera.posture].filter(Boolean) : [];
      const who = t.face ? faces.name(t.face.id) || "registered" : "";
      const text = `${isTarget ? "TARGET " : inGroup ? "GROUP " : ""}${who || t.id}${extra.length ? ` · ${extra.join(" · ")}` : ""}`
        + `${wait !== undefined && !isTarget ? ` · auto select in ${wait.toFixed(1)} s` : ""}`;
      label(ctx, text, bx, by, isTarget ? "#d9730d" : inGroup ? "rgba(217,115,13,0.6)" : "rgba(0,0,0,0.65)", dpr);
    }

    // Registration frame (Registered Face Tracking): turns green when the face in it can be registered. In the centre,
    // or around the head of the person chosen by clicking them (it follows them).
    if (faces.guide === camera.id || camera.face_guide) {
      const f = camera.face_guide?.frame;
      const choosing = faces.picking === camera.id;
      ctx.font = `${13 * dpr}px system-ui`;
      ctx.textAlign = "center";
      if (choosing) {
        ctx.textAlign = "start";
        label(ctx, "Click the person whose face to register", 8 * dpr, 40 * dpr, "rgba(0,0,0,0.75)", dpr);
      } else if (f || faces.picked === null) {
        const [gx, gy, gw, gh] = f ? [f[0] * w, f[1] * h, f[2] * w, f[3] * h]
                                   : [(w - 0.34 * h) / 2, (h - 0.34 * h) / 2, 0.34 * h, 0.34 * h];
        const ok = camera.face_guide?.ok ?? false;
        ctx.fillStyle = "rgba(0,0,0,0.45)";
        ctx.fillRect(0, 0, w, gy);
        ctx.fillRect(0, gy + gh, w, h - gy - gh);
        ctx.fillRect(0, gy, gx, gh);
        ctx.fillRect(gx + gw, gy, w - gx - gw, gh);
        ctx.strokeStyle = ok ? "#3ddc6c" : "#ffffff";
        ctx.lineWidth = 3 * dpr;
        ctx.strokeRect(gx, gy, gw, gh);
        ctx.fillStyle = ok ? "#3ddc6c" : "#ffffff";
        const text = ok ? "Face is usable: Capture"
          : camera.face_guide?.problem || (f ? "Face the camera" : "Face forward, centre your face, fill the frame");
        ctx.fillText(text, Math.min(Math.max(gx + gw / 2, 150 * dpr), w - 150 * dpr), Math.min(gy + gh + 20 * dpr, h - 8 * dpr));
      } else {  // a person was chosen: waiting for the first check of their face
        ctx.fillStyle = "#ffffff";
        ctx.fillText(camera.face_guide?.problem || "Looking for the chosen person's face…", w / 2, 40 * dpr);
      }
      ctx.textAlign = "start";
    }

    // The tracking badge: one target or several
    if (camera.state === "tracking" || camera.state === "angle_fixed") {
      const n = 1 + (camera.group?.length ?? 0);
      label(ctx, n > 1 ? `Tracking ${n} people` : "Tracking 1 person", 6 * dpr, 22 * dpr, "rgba(0,0,0,0.6)", dpr);
    }

    // What the tracker is doing right now
    const banner = camera.paused ? ["Manual operation: tracking paused", "#d4ac0d"]
      : camera.state === "angle_fixed" ? [`Angle fixed (fixed viewing angle ${(camera.fixed ?? 0) + 1})`, "#0a8a2e"]
      : camera.moving ? [`Moving: ${camera.moving}`, "rgba(0,0,0,0.6)"] : null;
    if (banner) {
      ctx.font = `${12 * dpr}px system-ui`;
      const tw = ctx.measureText(banner[0]).width + 16 * dpr;
      ctx.fillStyle = banner[1];
      ctx.fillRect((w - tw) / 2, 6 * dpr, tw, 20 * dpr);
      ctx.fillStyle = "#fff";
      ctx.fillText(banner[0], (w - tw) / 2 + 8 * dpr, 20 * dpr);
    }
  }

  // --- pointer: what a click or a drag does depends on the mouse mode ---------------------

  /** The person at a point of the picture (0..1): the smallest box around it, else the nearest within 15 % of
   *  the width (like the engine's target select). */
  function personAt(x: number, y: number): number | null {
    const fw = camera.frame.width, fh = camera.frame.height;
    if (!fw || !fh) return null;
    const px = x * fw, py = y * fh;
    const inside = camera.tracks.filter((t) => px >= t.box[0] && px <= t.box[2] && py >= t.box[1] && py <= t.box[3]);
    if (inside.length) {
      return inside.reduce((a, b) => ((a.box[2] - a.box[0]) * (a.box[3] - a.box[1]) <= (b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) ? a : b)).id;
    }
    let best: number | null = null, bestD = 0.15 * fw;
    for (const t of camera.tracks) {
      const d = Math.hypot((t.box[0] + t.box[2]) / 2 - px, (t.box[1] + t.box[3]) / 2 - py);
      if (d < bestD) [best, bestD] = [t.id, d];
    }
    return best;
  }

  function point(e: PointerEvent): { x: number; y: number; px: number; py: number } {
    const r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height, px: r.width, py: r.height };
  }

  function hitArea(x: number, y: number, pw: number, ph: number): Drag | null {
    const edge = 10; // CSS pixels
    const list = (camera.areas ?? []).filter((a) => editable.includes(a.kind) && (!shown || shown.includes(a.kind)));
    for (const a of [...list].reverse()) {
      const [x1, y1, x2, y2] = rectOf(a);
      const nl = Math.abs(x - x1) * pw < edge, nr = Math.abs(x - x2) * pw < edge;
      const nt = Math.abs(y - y1) * ph < edge, nb = Math.abs(y - y2) * ph < edge;
      const insideX = x > x1 - edge / pw && x < x2 + edge / pw, insideY = y > y1 - edge / ph && y < y2 + edge / ph;
      if (!insideX || !insideY) continue;
      const v = nt ? "t" : nb ? "b" : "", hz = nl ? "l" : nr ? "r" : "";
      const mode = (hz + v || "move") as Extract<Drag, { what: "area" }>["mode"];
      return { what: "area", area: a, mode, x0: x, y0: y, rect: [x1, y1, x2, y2] };
    }
    return null;
  }

  function onpointerdown(e: PointerEvent) {
    if (!interactive) return;
    const p = point(e);
    if (ui.mouseMode === "area") {
      drag = hitArea(p.x, p.y, p.px, p.py);
    } else if (ui.mouseMode === "silhouette") {
      drag = { what: "silhouette", x: p.x, y: p.y };
      silhouetteAt = anchor();
    } else {
      drag = { what: "click", x: p.x, y: p.y };
    }
    if (drag) {
      canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
  }

  function onpointermove(e: PointerEvent) {
    if (!drag) return;
    const p = point(e);
    if (drag.what === "area") {
      const dx = p.x - drag.x0, dy = p.y - drag.y0;
      let [x1, y1, x2, y2] = drag.rect;
      const m = drag.mode;
      if (m === "move") { x1 += dx; x2 += dx; y1 += dy; y2 += dy; }
      if (m.includes("l")) x1 = Math.min(x2 - 0.02, x1 + dx);
      if (m.includes("r")) x2 = Math.max(x1 + 0.02, x2 + dx);
      if (m.includes("t")) y1 = Math.min(y2 - 0.02, y1 + dy);
      if (m.includes("b")) y2 = Math.max(y1 + 0.02, y2 + dy);
      override = { kind: drag.area.kind, index: drag.area.index, rect: [x1, y1, x2, y2] };
    } else if (drag.what === "silhouette") {
      // the display position can't be at the edges of the picture
      silhouetteAt = { x: Math.min(0.85, Math.max(0.15, p.x)), y: Math.min(0.7, Math.max(0.1, p.y)) };
      if (performance.now() - lastSent > 120) sendSilhouette();
    }
  }

  /** Settings changes go through the settings store when it holds this camera (it shows them at once). */
  function settingsCommand(cmd: Record<string, unknown>) {
    if (slots.camera === camera.id) {
      return cmd.type === "slot_settings" ? slots.patch(cmd.settings as Record<string, unknown>, camera.id)
        : slots.command(cmd, true, camera.id);
    }
    return live.request({ camera: camera.id, ...cmd }).then((reply) => {
      if (!reply.ok) ui.notify(reply.error ?? "Not possible");
      return reply;
    });
  }

  function sendSilhouette() {
    if (!silhouetteAt) return;
    lastSent = performance.now();
    settingsCommand({ type: "slot_settings", settings: { composition: { anchor_x: +silhouetteAt.x.toFixed(3), anchor_y: +silhouetteAt.y.toFixed(3) } } });
  }

  async function onpointerup(e: PointerEvent) {
    const d = drag;
    drag = null;
    if (!d) return;
    const p = point(e);
    if (d.what === "area" && override) {
      const o = override;
      await settingsCommand({ type: "area", kind: o.kind, index: o.index, rect: o.rect.map((v) => +v.toFixed(4)) });
      override = null;
    } else if (d.what === "silhouette") {
      sendSilhouette();
      setTimeout(() => (silhouetteAt = null), 400); // until the status shows the new position
    } else if (d.what === "click" && Math.hypot(p.x - d.x, p.y - d.y) < 0.02) {
      if (faces.picking === camera.id) {  // choosing whose face to register
        const person = personAt(p.x, p.y);
        if (person === null) ui.notify("Nobody there: click a person");
        else await faces.pickPerson(camera.id, person);
      } else if (ui.mouseMode === "center") {
        const reply = await live.request({ type: "click_center", camera: camera.id, x: p.x, y: p.y });
        if (!reply.ok) ui.notify(reply.error ?? "Can't move there");
      } else {
        const reply = await live.request({ type: "select", camera: camera.id, x: p.x, y: p.y });
        if (reply.ok && reply.target === null && reply.message) ui.notify(reply.message);
      }
    }
  }
</script>

<canvas bind:this={canvas} {onpointerdown} {onpointermove} {onpointerup} onpointercancel={() => { drag = null; override = null; }}
        class:interactive class="mode-{ui.mouseMode}" aria-label="{camera.name} live picture"></canvas>

<style>
  canvas { width: 100%; aspect-ratio: 16 / 9; display: block; background: #000; touch-action: manipulation; }
  canvas.mode-area, canvas.mode-silhouette { touch-action: none; }
  canvas.interactive { cursor: crosshair; }
  canvas.interactive.mode-area, canvas.interactive.mode-silhouette { cursor: move; }
</style>

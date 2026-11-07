<script lang="ts">
  import type { Snippet } from "svelte";
  import { live, type CameraStatus } from "../lib/live.svelte";
  import { holdToMove } from "../lib/hold";

  // The settings pages' camera controls around the picture: pan slider under the
  // video (◀ ▶ fine buttons), tilt slider on the right (▲ ▼), zoom slider at the far right
  // (up = telephoto, T / W). Sliders move the camera to an absolute position; the fine buttons
  // move it while held.
  let { camera, children }: { camera: CameraStatus; children: Snippet } = $props();
  const ZOOM_MAX = 16384; // raw lens value; matches the engine's default lens
  let dragging = $state<{ pan?: number; tilt?: number; zoom?: number } | null>(null);
  let lastSent = 0;
  let pose = $derived(camera.pose);
  let pan = $derived(dragging?.pan ?? pose?.pan ?? 0);
  let tilt = $derived(dragging?.tilt ?? pose?.tilt ?? 0);
  let zoom = $derived(dragging?.zoom ?? pose?.zoom ?? 0);

  function move(values: { pan?: number; tilt?: number; zoom?: number }, final = false) {
    dragging = { ...dragging, ...values };
    if (!final && performance.now() - lastSent < 200) return;
    lastSent = performance.now();
    live.send({ type: "goto", camera: camera.id, ...dragging });
    if (final) setTimeout(() => (dragging = null), 1500); // until the camera reports its new position
  }
  const hold = (p: number, t: number, z: number) => holdToMove(() => camera.id, () => [p, t, z]);
  const value = (e: Event) => +(e.currentTarget as HTMLInputElement).value;
</script>

<div class="frame">
  <div class="video">{@render children()}</div>
  <div class="tilt">
    <button {...hold(0, 3, 0)} aria-label="tilt up">▲</button>
    <input type="range" class="vertical" min="-90" max="90" step="0.1" value={tilt} aria-label="tilt"
           oninput={(e) => move({ tilt: value(e) })} onchange={(e) => move({ tilt: value(e) }, true)} />
    <button {...hold(0, -3, 0)} aria-label="tilt down">▼</button>
  </div>
  <div class="zoom">
    <button {...hold(0, 0, 2)} aria-label="zoom in">T</button>
    <input type="range" class="vertical blue" min="0" max={ZOOM_MAX} step="16" value={zoom} aria-label="zoom"
           oninput={(e) => move({ zoom: value(e) })} onchange={(e) => move({ zoom: value(e) }, true)} />
    <button {...hold(0, 0, -2)} aria-label="zoom out">W</button>
  </div>
  <div class="pan">
    <button {...hold(-3, 0, 0)} aria-label="pan left">◀</button>
    <input type="range" min="-170" max="170" step="0.1" value={pan} aria-label="pan"
           oninput={(e) => move({ pan: value(e) })} onchange={(e) => move({ pan: value(e) }, true)} />
    <button {...hold(3, 0, 0)} aria-label="pan right">▶</button>
  </div>
  <div class="readout mono">
    {pose ? `Pan ${pose.pan.toFixed(1)}°  Tilt ${pose.tilt.toFixed(1)}°  Field of view ${pose.hfov.toFixed(1)}°` : "camera position unknown"}
  </div>
</div>

<style>
  .frame { display: grid; grid-template-columns: minmax(0, 1fr) 44px 44px; grid-template-rows: auto auto auto; gap: 6px; }
  .video { grid-column: 1; grid-row: 1; min-width: 0; }
  .tilt, .zoom { grid-row: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .tilt { grid-column: 2; }
  .zoom { grid-column: 3; }
  .pan { grid-column: 1; grid-row: 2; display: flex; gap: 6px; align-items: center; }
  .pan input { flex: 1; }
  .readout { grid-column: 1 / -1; grid-row: 3; font-size: 12px; color: var(--dim); }
  button { width: 40px; min-height: 36px; padding: 0; touch-action: none; user-select: none; }
  input[type="range"] { accent-color: #fff; padding: 0; background: none; border: none; min-height: 24px; }
  input.vertical { writing-mode: vertical-lr; direction: rtl; flex: 1; width: 24px; min-height: 120px; }
  input.blue { accent-color: var(--blue); }
</style>

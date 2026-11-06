<script lang="ts">
  import { live } from "../lib/live.svelte";
  import { holdToMove } from "../lib/hold";
  import NumberSlider from "../ui/NumberSlider.svelte";

  // Manual pan/tilt/zoom. Commands repeat while a button is held; releasing it stops the camera
  // (and the engine stops it anyway if the repeats stop arriving).
  let { camera }: { camera: string } = $props();
  let speed = $state(4);
  let zoomSpeed = $state(3);

  const hold = (pan: number, tilt: number, zoom = 0) =>
    holdToMove(() => camera, () => [pan * speed, tilt * speed, zoom * zoomSpeed]);
  const arrows: [string, number, number][] = [
    ["◤", -1, 1], ["▲", 0, 1], ["◥", 1, 1], ["◀", -1, 0], ["", 0, 0], ["▶", 1, 0], ["◣", -1, -1], ["▼", 0, -1], ["◢", 1, -1],
  ];
</script>

<div class="ptz">
  <div class="row">
    <div class="pad">
      {#each arrows as [label, pan, tilt]}
        {#if label}<button {...hold(pan, tilt)} aria-label="move {label}">{label}</button>{:else}<span></span>{/if}
      {/each}
    </div>
    <div class="zoom">
      <button {...hold(0, 0, 1)}>T</button>
      <button {...hold(0, 0, -1)}>W</button>
    </div>
  </div>
  <label>Pan/Tilt speed <NumberSlider value={speed} min={1} max={18} onchange={(v) => (speed = v)} /></label>
  <label>Zoom speed <NumberSlider value={zoomSpeed} min={1} max={8} onchange={(v) => (zoomSpeed = v)} /></label>
  <div class="presets">
    {#each [1, 2, 3, 4, 5, 6, 7, 8, 9] as n}
      <button onclick={() => live.send({ type: "preset", camera, number: n })}>{n}</button>
    {/each}
  </div>
  <p class="hint">Moving the camera here while tracking counts as a manual operation: tracking stops unless
    "Restart Tracking After Manual Operation" is on (Setting Wizard → Tracking Target Auto Select). Presets follow the Camera Preset Link.</p>
</div>

<style>
  .ptz { display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; gap: 16px; align-items: center; justify-content: center; }
  .pad { display: grid; grid-template-columns: repeat(3, 48px); gap: 4px; }
  .pad button, .zoom button { height: 46px; padding: 0; font-size: 16px; touch-action: none; user-select: none; }
  .zoom { display: grid; grid-template-rows: repeat(2, 46px); gap: 8px; width: 56px; }
  label { font-size: 12px; color: var(--dim); display: flex; flex-direction: column; gap: 2px; }
  .presets { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
  .presets button { padding: 0; height: 40px; }
  .hint { font-size: 12px; color: var(--dim); margin: 0; }
</style>

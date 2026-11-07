<script lang="ts">
  import { live } from "../lib/live.svelte";
  import CameraTile from "./CameraTile.svelte";

  // The cameras side by side, two to a row (one on a narrow screen), each box sized automatically or by dragging
  // its corner (CameraTile). Double-click one to show it large, double-click again for all of them.
  let { selected, onselect }: { selected: string | null; onselect: (id: string) => void } = $props();
  let focus = $state<string | null>(null);
  let cameras = $derived(live.status?.cameras ?? []);
  let shown = $derived(focus ? cameras.filter((c) => c.id === focus) : cameras);
</script>

<div class="grid" class:single={focus !== null || cameras.length <= 1}>
  {#each shown as camera (camera.id)}
    <CameraTile {camera} index={cameras.indexOf(camera)} selected={camera.id === selected} focused={focus !== null}
                onselect={() => onselect(camera.id)} onfocus={() => (focus = focus ? null : camera.id)} />
  {/each}
</div>

<style>
  .grid { display: flex; flex-wrap: wrap; gap: 10px; padding: 10px; align-content: flex-start; align-items: flex-start;
          overflow-y: auto; }
  /* automatic size: two boxes to a row; a dragged size is set on the box itself */
  .grid > :global(.tile) { width: calc((100% - 10px) / 2); max-width: 100%; }
  .grid.single { justify-content: center; }
  .grid.single > :global(.tile) { width: 100%; }
  /* Where the view has its own height (not on a phone, where the page scrolls): no box grows taller than the view,
     so its whole picture always shows (header and info rows ~92px; --aspect: the camera picture's shape). */
  @media (min-width: 761px) {
    .grid { container-type: size; }
    .grid > :global(.tile) { max-width: min(100%, calc((100cqh - 20px - 92px) * var(--aspect, 16 / 9))); }
  }
  @media (max-width: 900px) { .grid > :global(.tile) { width: 100%; } }
</style>

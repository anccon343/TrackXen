<script lang="ts">
  import { live } from "../lib/live.svelte";
  import CameraTile from "./CameraTile.svelte";

  // 2x2 grid of cameras (double-click one to show it large, double-click again for the grid).
  let { selected, onselect }: { selected: string | null; onselect: (id: string) => void } = $props();
  let focus = $state<string | null>(null);
  let cameras = $derived(live.status?.cameras ?? []);
  let shown = $derived(focus ? cameras.filter((c) => c.id === focus) : cameras);
</script>

<div class="grid" class:single={focus !== null || cameras.length <= 1}>
  {#each shown as camera (camera.id)}
    <CameraTile {camera} index={cameras.indexOf(camera)} selected={camera.id === selected}
                onselect={() => onselect(camera.id)} onfocus={() => (focus = focus ? null : camera.id)} />
  {/each}
  {#if !focus}
    {#each Array(Math.max(0, 4 - cameras.length)) as _, i}
      <div class="empty"><span class="num">{cameras.length + i + 1}</span><span class="dim">Not set up</span></div>
    {/each}
  {/if}
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 10px; align-content: start;
          overflow-y: auto; }
  .grid.single { grid-template-columns: minmax(0, 1fr); }
  /* one picture: as large as fits, header and info rows included (the canvas keeps 16:9). Only where the
     view has a fixed height; on a phone the page scrolls instead. */
  @media (min-width: 761px) {
    .grid.single { container-type: size; align-items: start; }
    .grid.single > :global(.tile) { width: min(100%, calc((100cqh - 20px - 92px) * 16 / 9)); justify-self: center; }
  }
  .grid.single .empty { display: none; }
  .empty { background: var(--panel-2); border-radius: var(--radius); aspect-ratio: 16 / 10; display: flex; flex-direction: column;
           gap: 8px; align-items: center; justify-content: center; }
  .num { width: 28px; height: 28px; display: grid; place-items: center; background: #444; color: var(--dim); font-weight: 700; border-radius: 3px; }
  @media (max-width: 900px) { .grid { grid-template-columns: minmax(0, 1fr); } }
</style>

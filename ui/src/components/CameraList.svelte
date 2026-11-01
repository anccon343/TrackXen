<script lang="ts">
  import { live } from "../lib/live.svelte";
  import VideoCanvas from "./VideoCanvas.svelte";

  // Left "Camera List" with small live thumbnails, like Canon's remote camera application.
  let { selected, onselect }: { selected: string | null; onselect: (id: string) => void } = $props();
  let cameras = $derived(live.status?.cameras ?? []);
  let problems = $derived(live.status?.problems ?? []);
  const level = (id: string) =>
    problems.some((p) => p.source === id && p.level === "error") ? "error"
      : problems.some((p) => p.source === id) ? "warning" : "ok";
</script>

<aside class="list">
  <h2>Camera List</h2>
  {#each cameras as camera (camera.id)}
    <button class="card" class:selected={camera.id === selected} onclick={() => onselect(camera.id)}>
      <div class="title"><span class="light {level(camera.id)}"></span>{camera.name}</div>
      <VideoCanvas {camera} interactive={false} overlays={false} />
    </button>
  {:else}
    <p class="dim">No cameras yet. Add them in Settings → Cameras.</p>
  {/each}
</aside>

<style>
  .list { background: var(--rail); padding: 8px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
  h2 { font-size: 13px; margin: 2px 2px 4px; color: var(--dim); font-weight: 600; }
  .card { display: block; text-align: left; padding: 6px; background: var(--panel-2); border: none;
          border-left: 4px solid transparent; border-radius: var(--radius); }
  .card.selected { border-left-color: var(--accent); }
  .title { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-weight: 600; font-size: 13px; }
</style>

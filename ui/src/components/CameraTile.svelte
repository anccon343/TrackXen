<script lang="ts">
  import { fmt, live, stat, type CameraStatus } from "../lib/live.svelte";
  import { STATE_TEXT } from "../lib/ui.svelte";
  import VideoCanvas from "./VideoCanvas.svelte";

  // One camera in the multi-view, like a slot in Canon's Remote Camera Control Application.
  let { camera, index, selected = false, onselect, onfocus }: {
    camera: CameraStatus; index: number; selected?: boolean; onselect: () => void; onfocus: () => void;
  } = $props();

  let sample = $derived(live.status?.stats);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<section class="tile" class:selected class:live={camera.on_air} onclick={onselect} ondblclick={onfocus}>
  <header>
    <span class="num" class:active={camera.tracking}>{index + 1}</span>
    <span class="name">{camera.name}</span>
    {#if camera.on_air}<span class="badge live-badge" title={camera.on_air_manual === null ? "On Program in vMix" : "On air (manual switch)"}>LIVE</span>{/if}
    <span class="badge state-{camera.state}">{STATE_TEXT[camera.state] ?? camera.state}</span>
    <button class:on={camera.tracking} onclick={(e) => { e.stopPropagation(); live.send({ type: "tracking", camera: camera.id, on: !camera.tracking }); }}>
      Auto Tracking {camera.tracking ? "ON" : "OFF"}
    </button>
  </header>
  <div class="info top">
    <span>{camera.source?.source ?? "no source"}</span>
    <span class="mono">{fmt(stat(sample, camera.id, "source_fps"))} fps</span>
  </div>
  <VideoCanvas {camera} />
  <div class="info mono">
    <span>{camera.pose ? `Pan ${fmt(camera.pose.pan)}°  Tilt ${fmt(camera.pose.tilt)}°  FOV ${fmt(camera.pose.hfov)}°` : "no position"}</span>
    <span>{camera.target !== null ? `Target ${camera.target}` : "No target"} · {camera.tracks.length} people</span>
  </div>
</section>

<style>
  .tile { background: var(--panel); border: 2px solid transparent; border-radius: var(--radius); overflow: hidden;
          display: flex; flex-direction: column; min-width: 0; }
  .tile.selected { border-color: var(--accent); }
  .tile.live { border-color: var(--tally-program); box-shadow: 0 0 0 1px var(--tally-program); }
  .live-badge { background: var(--tally-program); }
  header { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--panel-2); }
  .num { width: 28px; height: 28px; display: grid; place-items: center; background: #555; font-weight: 700;
         border-radius: 3px; flex: none; }
  .num.active { background: var(--accent); }
  .name { flex: 1; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  header button { min-height: 28px; font-size: 12px; padding: 4px 10px; }
  .info { display: flex; justify-content: space-between; gap: 8px; padding: 4px 8px; font-size: 12px;
          color: var(--dim); white-space: nowrap; overflow: hidden; }
  .info.top { padding-top: 2px; padding-bottom: 2px; }
</style>

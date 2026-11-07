<script lang="ts">
  import { fmt, live, stat, type CameraStatus } from "../lib/live.svelte";
  import { tileSizes } from "../lib/tileSizes.svelte";
  import { STATE_TEXT } from "../lib/ui.svelte";
  import VideoCanvas from "./VideoCanvas.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // One camera in the multi-view. Its size: automatic, or dragged at its bottom-right corner (kept on this device).
  // The picture keeps the camera's own shape, so it is never cropped or stretched, and a box never grows past the
  // view: its whole picture always fits.
  let { camera, index, selected = false, focused = false, onselect, onfocus }: {
    camera: CameraStatus; index: number; selected?: boolean; focused?: boolean; onselect: () => void; onfocus: () => void;
  } = $props();

  const MIN_WIDTH = 240; // narrower, and the header's name and Auto Tracking switch get cramped
  const KEY_STEP = 40;
  let sample = $derived(live.status?.stats);
  let aspect = $derived(camera.frame?.width && camera.frame?.height ? camera.frame.width / camera.frame.height : 16 / 9);
  let width = $derived(focused ? undefined : tileSizes.widths[camera.id]);
  let tile: HTMLElement;
  let drag: { left: number; top: number; chrome: number; max: number } | null = null;

  /** The widest this box may get: the view's width, and on a large screen also its height (the picture with the
   *  header and info rows must fit). On a phone the page scrolls, so only the width counts. */
  function widest(chrome: number): number {
    const view = tile.parentElement!;
    const byWidth = view.clientWidth - 20; // (the view's padding)
    const sized = getComputedStyle(view).containerType === "size";
    return Math.max(MIN_WIDTH, sized ? Math.min(byWidth, (view.clientHeight - 20 - chrome) * aspect) : byWidth);
  }

  function chromeOf(r: DOMRect): number {
    return r.height - r.width / aspect; // header and info rows: all of the box but the picture
  }

  function startResize(e: PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    const r = tile.getBoundingClientRect();
    const chrome = chromeOf(r);
    drag = { left: r.left, top: r.top, chrome, max: widest(chrome) };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function moveResize(e: PointerEvent) {
    if (!drag) return;
    const x = e.clientX - drag.left, y = e.clientY - drag.top;
    // the box's corner, (w, chrome + w / aspect), nearest to the pointer
    const w = (x + (y - drag.chrome) / aspect) / (1 + 1 / (aspect * aspect));
    tileSizes.set(camera.id, Math.min(drag.max, Math.max(MIN_WIDTH, w)));
  }

  function endResize() {
    if (!drag) return;
    drag = null;
    tileSizes.save();
  }

  function keyResize(e: KeyboardEvent) {
    const step = ({ ArrowRight: KEY_STEP, ArrowDown: KEY_STEP, ArrowLeft: -KEY_STEP, ArrowUp: -KEY_STEP } as Record<string, number>)[e.key];
    if (!step) return;
    e.preventDefault();
    const r = tile.getBoundingClientRect();
    tileSizes.set(camera.id, Math.min(widest(chromeOf(r)), Math.max(MIN_WIDTH, r.width + step)));
    tileSizes.save();
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<section class="tile" class:selected class:live={camera.on_air} bind:this={tile} style:--aspect={aspect}
         style:width={width ? `${width}px` : undefined} onclick={onselect} ondblclick={onfocus}>
  <header>
    <span class="num" class:active={camera.tracking}>{index + 1}</span>
    <span class="name">{camera.name}</span>
    {#if camera.on_air}<span class="badge live-badge" title={camera.on_air_manual === null ? "On Program in vMix" : "On air (manual switch)"}>LIVE</span>{/if}
    {#if camera.state !== "idle"}<span class="badge state-{camera.state}">{STATE_TEXT[camera.state] ?? camera.state}</span>{/if}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <span class="auto" onclick={(e) => e.stopPropagation()} ondblclick={(e) => e.stopPropagation()}>
      <span class="dim">Auto Tracking</span>
      <Toggle checked={camera.tracking} onchange={(on) => live.send({ type: "tracking", camera: camera.id, on })} />
    </span>
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
  {#if !focused}
    <button type="button" class="grip" aria-label="{camera.name}: box size (arrow keys)"
            title="Drag to resize · double-click: automatic size"
            onpointerdown={startResize} onpointermove={moveResize} onpointerup={endResize} onpointercancel={endResize}
            onclick={(e) => e.stopPropagation()} ondblclick={(e) => { e.stopPropagation(); tileSizes.clear(camera.id); }}
            onkeydown={keyResize}></button>
  {/if}
</section>

<style>
  .tile { position: relative; background: var(--panel); border: 2px solid transparent; border-radius: var(--radius);
          overflow: hidden; display: flex; flex-direction: column; min-width: 0; }
  .grip { position: absolute; right: 0; bottom: 0; width: 20px; height: 20px; min-height: 0; padding: 0; border: 0;
          border-radius: 0; cursor: nwse-resize; touch-action: none; opacity: 0.6;
          background: linear-gradient(135deg, transparent 45%, var(--dim) 45% 52%, transparent 52% 64%, var(--dim) 64% 71%,
                                      transparent 71% 83%, var(--dim) 83% 90%, transparent 90%); }
  .grip:hover, .grip:focus-visible { opacity: 1; background-color: transparent; }
  .tile.selected { border-color: var(--accent); }
  .tile.live { border-color: var(--tally-program); box-shadow: 0 0 0 1px var(--tally-program); }
  .live-badge { background: var(--tally-program); }
  header { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--panel-2); }
  .num { width: 28px; height: 28px; display: grid; place-items: center; background: #555; font-weight: 700;
         border-radius: 3px; flex: none; }
  .num.active { background: var(--accent); }
  .name { flex: 1; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .auto { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; }
  .info { flex: none; display: flex; justify-content: space-between; gap: 8px; padding: 4px 8px; font-size: 12px;
          color: var(--dim); white-space: nowrap; overflow: hidden; }
  .info.top { padding-top: 2px; padding-bottom: 2px; }
</style>

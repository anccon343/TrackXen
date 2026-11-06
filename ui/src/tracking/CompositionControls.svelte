<script lang="ts">
  import { slots, type AppSettings } from "../lib/slots.svelte";
  import { ui } from "../lib/ui.svelte";
  import NumberSlider from "../ui/NumberSlider.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Canon's Composition settings (wizard step 5, also on the Tracking Execution screen), and beyond Canon
  // Pan/Tilt Smoothness.
  let { slot, help = false }: { slot: AppSettings; help?: boolean } = $props();
  let c = $derived(slot.composition);
  let sitStand = $derived(slot.auto_framing.enabled && slot.auto_framing.sit_stand.enabled);
  const comp = (values: Record<string, number | boolean>) => slots.patch({ composition: values });
</script>

<h3 class="sub first">Framing</h3>
<div class="row"><span>Display Position</span>
  <button class="small" class:on={ui.mouseMode === "silhouette"}
          onclick={() => (ui.mouseMode = ui.mouseMode === "silhouette" ? "select" : "silhouette")}>
    {ui.mouseMode === "silhouette" ? "Dragging silhouette" : "Drag silhouette"}</button></div>
{#if help}<p class="help">Drag the silhouette in the picture to where the target's face should be. Not at the edges: the target is lost more easily there. A priority display area overrides it.</p>{/if}
<NumberSlider value={c.anchor_x} min={0.15} max={0.85} step={0.01} digits={2} minLabel="left" maxLabel="right"
              onchange={(v) => comp({ anchor_x: v })} />
<NumberSlider value={c.anchor_y} min={0.1} max={0.7} step={0.01} digits={2} minLabel="top" maxLabel="low"
              onchange={(v) => comp({ anchor_y: v })} />

<div class="row"><span>Display Size</span><Toggle checked={c.display_size} disabled={sitStand} onchange={(on) => comp({ display_size: on })} /></div>
{#if sitStand}<p class="help">Set by Sit/Stand (Auto Framing): the standing size is used.</p>{/if}
{#if help}<p class="help">On: when a target is locked the camera zooms until the person is this tall in the picture (the silhouette shows the size).</p>{/if}
<NumberSlider value={c.target_height} min={0.2} max={0.95} step={0.05} digits={2} minLabel="small" maxLabel="large"
              disabled={!c.display_size || sitStand} onchange={(v) => comp({ target_height: v })} />
<div class="row"><span>Auto Zoom</span>
  <Toggle checked={c.auto_zoom} disabled={!c.display_size || sitStand} onchange={(on) => comp({ auto_zoom: on })} /></div>
{#if help}<p class="help">Keep zooming during tracking so the display size stays the same.</p>{/if}

<h3 class="sub">Movement</h3>
<div class="row"><span>Pan/Tilt Halting Area</span>
  <Toggle checked={slot.halting_area} onchange={(on) => slots.patch({ halting_area: on })} /></div>
{#if help}<p class="help">The camera stays still while the target's face is inside this frame around the display position. Smaller is livelier. Off: movement follows the sensitivity alone. Re-check it after changing the display size.</p>{/if}
<NumberSlider value={c.dead_zone_x} min={0.01} max={0.3} step={0.01} digits={2} minLabel="narrow" maxLabel="wide"
              disabled={!slot.halting_area} onchange={(v) => comp({ dead_zone_x: v })} />
<NumberSlider value={c.dead_zone_y} min={0.01} max={0.3} step={0.01} digits={2} minLabel="low" maxLabel="tall"
              disabled={!slot.halting_area} onchange={(v) => comp({ dead_zone_y: v })} />

<span class="label">Tracking Sensitivity</span>
<NumberSlider value={slot.sensitivity} min={1} max={10} step={1} onchange={(v) => slots.patch({ sensitivity: v })} />
{#if help}<p class="help">Higher follows more nimbly but may jump to people nearby. 1 is safest when changing target or moving the silhouette far.</p>{/if}

<span class="label">Pan/Tilt Smoothness</span>
<NumberSlider value={slot.smoothness} min={1} max={10} step={1} minLabel="lively" maxLabel="smooth"
              onchange={(v) => slots.patch({ smoothness: v })} />
{#if help}<p class="help">Higher starts, stops and changes speed more softly, also for this app's arrow buttons. 1 is the liveliest. High values follow sudden moves later, mostly in tight shots.</p>{/if}

<style>
  .row { display: flex; justify-content: space-between; align-items: center; min-height: 34px; gap: 8px; }
  .label { font-size: 12px; color: var(--dim); margin-top: 6px; }
  .help { font-size: 12px; color: var(--dim); margin: 0 0 4px; }
  .small { min-height: 28px; padding: 2px 10px; font-size: 12px; }
  /* where the person sits in the picture, then how the camera moves to keep them there */
  .sub { font-size: 11px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: var(--dim);
         margin: 12px 0 2px; }
  .sub.first { margin-top: 2px; }
</style>

<script lang="ts">
  import { slots, type AppSettings } from "../lib/slots.svelte";
  import NumberSlider from "../ui/NumberSlider.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Beyond Canon: how this App Settings slot moves while the camera is live on Program.
  let { slot }: { slot: AppSettings } = $props();
  let oa = $derived(slot.on_air);
  const set = (values: Record<string, unknown>) => slots.patch({ on_air: values });
</script>

<h2 class="section">On-Air Motion (while live on Program)</h2>
<div class="row"><span>Calmer motion while live</span><Toggle checked={oa.enabled} onchange={(on) => set({ enabled: on })} /></div>
<p class="help">Live is known from vMix tally (Settings → vMix & mixer) or the On Air switch. The change is blended over a second.</p>
{#if oa.enabled}
  <span class="label">Top speed while live</span>
  <NumberSlider value={oa.speed} min={0.1} max={1} step={0.05} digits={2} minLabel="slow" maxLabel="normal" onchange={(v) => set({ speed: v })} />
  <span class="label">Starts and stops</span>
  <NumberSlider value={oa.accel} min={0.1} max={1} step={0.05} digits={2} minLabel="soft" maxLabel="normal" onchange={(v) => set({ accel: v })} />
  <span class="label">Pan/Tilt Halting Area grows by</span>
  <NumberSlider value={oa.dead_zone} min={1} max={3} step={0.1} digits={1} minLabel="same" maxLabel="3×" onchange={(v) => set({ dead_zone: v })} />
  <p class="help">Small movements of the speaker are ignored while live.</p>
  <span class="label">Zoom while live</span>
  <label class="radio"><input type="radio" checked={oa.zoom === "slow"} onchange={() => set({ zoom: "slow" })} /> Slow</label>
  <label class="radio"><input type="radio" checked={oa.zoom === "off"} onchange={() => set({ zoom: "off" })} /> No zooming</label>
  <span class="label">A new target while live (auto select, faces, return to the initial position)</span>
  <label class="radio"><input type="radio" checked={oa.retarget === "wait"} onchange={() => set({ retarget: "wait" })} /> Wait until off Program</label>
  <label class="radio"><input type="radio" checked={oa.retarget === "slow"} onchange={() => set({ retarget: "slow" })} /> Move slowly</label>
{/if}

<style>
  .row { display: flex; justify-content: space-between; align-items: center; min-height: 34px; gap: 8px; }
  .label { font-size: 12px; color: var(--dim); margin-top: 6px; display: block; }
  .help { font-size: 12px; color: var(--dim); margin: 0 0 4px; }
  .radio { display: flex; gap: 8px; align-items: center; min-height: 30px; }
  .radio input { min-height: 0; accent-color: var(--accent); }
</style>

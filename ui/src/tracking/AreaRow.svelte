<script lang="ts">
  import type { AreaKind } from "../lib/live.svelte";
  import { areaStyle } from "../lib/areas";
  import { slots, type Area } from "../lib/slots.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // One area row of the settings pages: colour, name, on/off and View (plus Capture for fixed
  // viewing angle areas, whose shot is the camera angle they hold).
  let { kind, index = 0, area, title = "", capture = false, disabled = false }: {
    kind: AreaKind; index?: number; area: Area; title?: string; capture?: boolean; disabled?: boolean;
  } = $props();
  let style = $derived(areaStyle(kind, index));
</script>

<div class="area-row">
  <span class="chip" style="border-color: {style.colour}"></span>
  <span class="name">{title || style.label}</span>
  <Toggle checked={area.enabled} {disabled} onchange={(on) => slots.command({ type: "area", kind, index, enabled: on })} />
  {#if capture}
    <button class="small" disabled={!area.enabled || disabled} title="Use the camera's current view as this area's shot"
            onclick={() => slots.command({ type: "capture", what: kind, index })}>Capture</button>
  {/if}
  <button class="small" disabled={!area.enabled} onclick={() => slots.command({ type: "view", what: kind, index }, false)}>View</button>
</div>

<style>
  .area-row { display: flex; align-items: center; gap: 8px; min-height: 38px; }
  .chip { width: 14px; height: 14px; border: 2px dashed; border-radius: 2px; flex: none; }
  .name { flex: 1; }
  .small { min-height: 28px; padding: 2px 10px; font-size: 12px; }
</style>

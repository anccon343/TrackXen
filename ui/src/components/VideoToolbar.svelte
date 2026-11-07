<script lang="ts">
  import type { CameraStatus } from "../lib/live.svelte";
  import { slots } from "../lib/slots.svelte";
  import { tileSizes } from "../lib/tileSizes.svelte";
  import { MOUSE_MODES, ui } from "../lib/ui.svelte";

  // Above the pictures: the mouse mode at the top left, View Initial Position at the
  // top right (for the selected camera).
  let { camera }: { camera: CameraStatus | undefined } = $props();
  $effect(() => slots.sync(camera));
  let slot = $derived(camera && slots.camera === camera.id ? slots.slot : undefined);
</script>

<div class="toolbar">
  <label class="mouse"><span class="dim">Mouse</span>
    <select bind:value={ui.mouseMode} aria-label="Mouse operation">
      {#each MOUSE_MODES as m}<option value={m.id}>{m.label}</option>{/each}
    </select></label>
  <span class="grow"></span>
  {#if tileSizes.custom}
    <button title="Every camera box back to its automatic size" onclick={() => tileSizes.reset()}>Auto size</button>
  {/if}
  {#if camera}
    <button disabled={!slot?.initial.set} title={slot?.initial.set ? `${camera.name}: go to the initial position` : "No initial position captured yet (Setting Wizard → Initial Position)"}
            onclick={() => slots.command({ type: "view", what: "initial" }, false)}>View Initial Position</button>
  {/if}
</div>

<style>
  .toolbar { display: flex; align-items: center; gap: 10px; padding: 8px 10px 0; }
  .mouse { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .mouse select { max-width: 230px; }
  .grow { flex: 1; }
  button { min-height: 32px; padding: 4px 12px; font-size: 13px; }
</style>

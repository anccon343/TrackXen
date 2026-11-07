<script lang="ts">
  import type { CameraStatus } from "../lib/live.svelte";
  import PtzPad from "./PtzPad.svelte";
  import TrackingPanel from "./TrackingPanel.svelte";

  // Right-hand panel with tabs: Tracking and PTZ for the selected camera.
  let { camera, onsetup }: { camera: CameraStatus | undefined; onsetup: (mode: "wizard" | "pages") => void } = $props();
  let tab = $state<"tracking" | "basic">("tracking");
</script>

<aside class="control">
  <nav>
    <button class:active={tab === "tracking"} onclick={() => (tab = "tracking")}>Tracking</button>
    <button class:active={tab === "basic"} onclick={() => (tab = "basic")}>PTZ</button>
  </nav>
  <div class="body">
    {#if !camera}
      <p class="dim">Select a camera.</p>
    {:else}
      <div class="camname">{camera.name}</div>
      {#if tab === "tracking"}<TrackingPanel {camera} {onsetup} />{:else}<PtzPad camera={camera.id} />{/if}
    {/if}
  </div>
</aside>

<style>
  .control { background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
  nav { display: flex; border-bottom: 1px solid var(--line); }
  nav button { flex: 1; background: none; border: none; border-bottom: 3px solid transparent; border-radius: 0;
               min-height: 42px; color: var(--dim); }
  nav button.active { color: var(--text); border-bottom-color: var(--accent); }
  .body { padding: 10px 12px; overflow-y: auto; flex: 1; }
  .camname { font-weight: 700; margin-bottom: 8px; }
</style>

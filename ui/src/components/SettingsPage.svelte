<script lang="ts">
  import { onMount } from "svelte";
  import { settings } from "../lib/config.svelte";
  import CameraSettings from "../settings/CameraSettings.svelte";
  import PerformanceSettings from "../settings/PerformanceSettings.svelte";
  import AdvancedSettings from "../settings/AdvancedSettings.svelte";
  import SystemSettings from "../settings/SystemSettings.svelte";
  import ConnectionsSettings from "../settings/ConnectionsSettings.svelte";
  import AudioSettings from "../settings/AudioSettings.svelte";

  // Settings in the style of the Canon camera web settings: navigation on the left, the page on
  // the right with green Apply and grey Clear, and a green dot on changed fields.
  const pages = [
    { id: "cameras", title: "Cameras", component: CameraSettings, applies: true },
    { id: "connections", title: "vMix & mixer", component: ConnectionsSettings, applies: false },
    { id: "audio", title: "Audio follow", component: AudioSettings, applies: false },
    { id: "performance", title: "Performance", component: PerformanceSettings, applies: true },
    { id: "advanced", title: "Advanced", component: AdvancedSettings, applies: false },
    { id: "system", title: "System", component: SystemSettings, applies: false },
  ];
  let { page = $bindable("cameras") }: { page?: string } = $props();
  let current = $derived(pages.find((p) => p.id === page)!);

  // Fresh settings each time the page opens (other devices may have changed them), unless this
  // device has changes not applied yet.
  onMount(() => {
    if (!settings.draft || !settings.dirty) settings.load();
  });
</script>

<div class="settings">
  <nav>
    {#each pages as p}
      <button class:active={p.id === page} onclick={() => (page = p.id)}>{p.title}</button>
    {/each}
  </nav>
  <section>
    <header>
      <h1>{current.title}</h1>
      {#if current.applies}
        <span class="dim">{settings.busy ? "Applying…" : settings.dirty ? "Unsaved changes" : ""}</span>
        <button class="primary" disabled={!settings.dirty || settings.busy} onclick={() => settings.apply()}>Apply</button>
        <button disabled={!settings.dirty || settings.busy} onclick={() => settings.clear()}>Clear</button>
      {/if}
    </header>
    {#if settings.error}<p class="error">{settings.error}</p>{/if}
    {#if settings.draft}
      <current.component />
    {:else}
      <p class="dim">Loading settings…</p>
    {/if}
  </section>
</div>

<style>
  .settings { display: grid; grid-template-columns: 200px 1fr; height: 100%; min-height: 0; }
  nav { background: var(--panel-2); display: flex; flex-direction: column; padding-top: 8px; }
  nav button { background: none; border: none; border-left: 4px solid transparent; border-radius: 0; text-align: left;
               padding: 10px 16px; min-height: var(--touch); }
  nav button.active { border-left-color: var(--accent); background: #2f2f2f; font-weight: 700; }
  section { padding: 12px 20px; overflow-y: auto; }
  header { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  h1 { font-size: 18px; margin: 0; flex: 1; }
  header button { min-width: 110px; }
  .error { color: #ff6b5b; }
  @media (max-width: 700px) {
    .settings { grid-template-columns: 1fr; }
    nav { flex-direction: row; overflow-x: auto; padding: 0; }
    nav button { border-left: none; border-bottom: 3px solid transparent; }
    nav button.active { border-bottom-color: var(--accent); }
  }
</style>

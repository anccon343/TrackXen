<script lang="ts">
  import { access } from "./lib/access.svelte";
  import { live } from "./lib/live.svelte";
  import { ui } from "./lib/ui.svelte";
  import PinScreen from "./components/PinScreen.svelte";
  import TopBar from "./components/TopBar.svelte";
  import CameraList from "./components/CameraList.svelte";
  import MultiView from "./components/MultiView.svelte";
  import VideoToolbar from "./components/VideoToolbar.svelte";
  import ControlPanel from "./components/ControlPanel.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import StatisticsPage from "./components/StatisticsPage.svelte";
  import SettingsPage from "./components/SettingsPage.svelte";
  import Wizard from "./tracking/Wizard.svelte";
  import Welcome from "./components/Welcome.svelte";

  type View = "cameras" | "statistics" | "settings";
  let view = $state<View>("cameras");
  let setup = $state<"wizard" | "pages" | null>(null); // the tracking settings wizard over the cameras view
  let settingsPage = $state("cameras");
  let selected = $state<string | null>(null);
  let cameras = $derived(live.status?.cameras ?? []);
  let camera = $derived(cameras.find((c) => c.id === selected) ?? cameras[0]);

  access.check().then(() => {
    if (!access.needed) live.connect();
  });
</script>

{#if access.needed}
  <PinScreen onsignedin={() => live.connect()} />
{:else}
<div class="app">
  <TopBar {view} onview={(v) => { view = v; setup = null; }}
          onperformance={() => { settingsPage = "performance"; view = "settings"; setup = null; }} />
  <main>
    {#if view === "cameras" && live.status && cameras.length === 0}
      <Welcome onsettings={(page) => { settingsPage = page; view = "settings"; }} />
    {:else if view === "cameras" && setup && camera}
      <Wizard {camera} mode={setup} onclose={() => (setup = null)} />
    {:else if view === "cameras"}
      <div class="cameras" class:one={cameras.length <= 1}>
        {#if cameras.length > 1}<CameraList selected={camera?.id ?? null} onselect={(id) => (selected = id)} />{/if}
        <div class="center">
          <VideoToolbar {camera} />
          <MultiView selected={camera?.id ?? null} onselect={(id) => (selected = id)} />
        </div>
        <ControlPanel {camera} onsetup={(mode) => (setup = mode)} />
      </div>
    {:else if view === "statistics"}
      <StatisticsPage />
    {:else}
      <SettingsPage bind:page={settingsPage} />
    {/if}
  </main>
  <StatusBar onstatistics={() => { view = "statistics"; setup = null; }} />
  {#if ui.message}<div class="toast" role="status">{ui.message}</div>{/if}
</div>
{/if}

<style>
  .app { height: 100%; display: grid; grid-template-rows: auto 1fr auto; }
  main { min-height: 0; overflow: hidden; }
  .cameras { display: grid; grid-template-columns: 200px minmax(0, 1fr) 320px; height: 100%; }
  .cameras > :global(*) { min-height: 0; }
  .cameras.one { grid-template-columns: minmax(0, 1fr) 320px; }  /* one camera: no list beside its own picture */
  .center { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .center > :global(.grid) { flex: 1; min-height: 0; }
  .toast { position: fixed; left: 50%; bottom: 44px; transform: translateX(-50%); background: #111; border: 1px solid var(--accent);
           border-radius: var(--radius); padding: 8px 14px; z-index: 30; max-width: calc(100vw - 32px); }
  @media (max-width: 1100px) {
    .cameras, .cameras.one { grid-template-columns: minmax(0, 1fr) 300px; }
    .cameras > :global(.list) { display: none; }
  }
  @media (max-width: 760px) {
    /* every picture, then the panel, each as tall as it is (no scrolling boxes of their own): the page scrolls, and
       the panel's Save bar sticks to the bottom of the screen */
    .cameras, .cameras.one { grid-template-columns: 1fr; grid-template-rows: max-content max-content; overflow-y: auto; }
  }
</style>

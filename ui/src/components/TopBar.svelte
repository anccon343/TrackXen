<script lang="ts">
  import { live } from "../lib/live.svelte";
  import { ui } from "../lib/ui.svelte";

  type View = "cameras" | "statistics" | "settings";
  let { view, onview }: { view: View; onview: (v: View) => void } = $props();
  let perf = $derived(live.status?.performance);

  // Over HTTP while the live connection is down; says so when it couldn't reach TrackXen.
  async function stopAll() {
    const reply = await live.requestReliably({ type: "stop_all" });
    if (!reply.ok) ui.notify(`STOP ALL didn't reach TrackXen (${reply.error ?? "no answer"}): press it again`);
  }
</script>

<header class="top">
  <span class="logo">Track<b>Xen</b></span>
  <nav>
    <button class:active={view === "cameras"} onclick={() => onview("cameras")}>Cameras</button>
    <button class:active={view === "statistics"} onclick={() => onview("statistics")}>Statistics</button>
    <button class:active={view === "settings"} onclick={() => onview("settings")}>Settings</button>
  </nav>
  <span class="grow"></span>
  {#if perf}<span class="mode" title="Performance mode (Settings → Performance)">{perf.mode.toUpperCase()}{perf.mode === "auto" ? ` · ${perf.preset}` : ""}</span>{/if}
  <span class="badge conn {live.connected ? 'state-tracking' : 'state-no_video'}" title={live.connected ? "Connected" : "Disconnected"}>
    <span class="txt">{live.connected ? "Connected" : "Disconnected"}</span></span>
  <button class="danger" onclick={stopAll} title="Stop tracking and stop every camera">STOP ALL</button>
</header>

<style>
  .top { display: flex; align-items: center; gap: 12px; padding: 0 12px; height: 48px; background: var(--rail);
         border-bottom: 1px solid var(--line); }
  .logo { font-size: 18px; letter-spacing: 0.5px; }
  .logo b { color: var(--accent); }
  nav { display: flex; gap: 2px; height: 100%; }
  nav button { background: none; border: none; border-bottom: 3px solid transparent; border-radius: 0; height: 100%;
               color: var(--dim); padding: 0 14px; }
  nav button.active { color: var(--text); border-bottom-color: var(--accent); }
  .grow { flex: 1; }
  .mode { font-size: 12px; font-weight: 700; color: var(--accent); border: 1px solid var(--accent); border-radius: 3px; padding: 2px 8px; }
  @media (max-width: 700px) { .logo, .mode { display: none; } nav button { padding: 0 8px; } .top { gap: 8px; padding: 0 8px; } }
  @media (max-width: 480px) {
    .conn { width: 14px; height: 14px; padding: 0; border-radius: 50%; }
    .conn .txt { display: none; }
    .danger { padding: 6px 8px; }
  }
</style>

<script lang="ts">
  import { faces } from "../lib/faces.svelte";
  import { live, postJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import { ui } from "../lib/ui.svelte";

  let confirmWipe = $state(false);
  let showFile = $state<File | null>(null);
  let loadingShow = $state(false);

  async function loadShow() {
    if (!showFile) return;
    loadingShow = true;
    try {
      await postJSON("/api/show", JSON.parse(await showFile.text()));
      await settings.load();
      ui.notify("Show file loaded: cameras restarted with it");
      showFile = null;
    } catch (e) {
      ui.notify(`Couldn't load the show file: ${e}`);
    } finally {
      loadingShow = false;
    }
  }
  $effect(() => faces.ensure());

  async function wipe() {
    await faces.wipe();
    confirmWipe = false;
  }
</script>

<h2 class="section">TrackXen</h2>
<div class="kv">
  <div><span>Version</span><span>0.1.0 (development)</span></div>
  <div><span>Vision engine</span><span>{live.status?.engine || "–"}</span></div>
  <div><span>Vision worker</span><span>{live.status?.worker_alive ? "running" : "not running"}</span></div>
  <div><span>Log file</span><span class="path">{live.status?.log_file ?? "–"}</span></div>
</div>
<h2 class="section">Show file</h2>
<p class="dim">The whole setup in one file: cameras, App Settings, preset links, vMix and audio-follow. Keep one per
  venue and load it there. Registered faces themselves are not in it (they stay on this PC).</p>
<a class="button" href="/api/show" download>Save setup to a file</a>
<div class="load">
  <input type="file" accept=".json,application/json" onchange={(e) => (showFile = (e.currentTarget as HTMLInputElement).files?.[0] ?? null)} />
  <button disabled={!showFile || loadingShow} onclick={loadShow}>{loadingShow ? "Loading…" : "Load setup"}</button>
</div>
<p class="dim">Loading replaces every setting and restarts the cameras.</p>

<h2 class="section">Face data</h2>
<p class="dim">{faces.list.length} registered face{faces.list.length === 1 ? "" : "s"}, stored only on this PC
  (names, face fingerprints and small thumbnails). Delete them when they are no longer needed, for
  example before the PC is given away.</p>
{#if confirmWipe}
  <p>Delete every registered face from every camera? This can't be undone.</p>
  <button onclick={() => (confirmWipe = false)}>Cancel</button>
  <button class="danger" onclick={wipe}>Delete all face data</button>
{:else}
  <button disabled={!faces.list.length} onclick={() => (confirmWipe = true)}>Delete all face data…</button>
{/if}
<h2 class="section">Tools</h2>
<p><a href="/test" target="_blank" rel="noopener">Phase 2 test page</a> (simple single-camera view)</p>
<p class="dim">TrackXen is open source under the AGPL-3.0 licence (it uses Ultralytics YOLO models).
  Face recognition: OpenCV YuNet and SFace (OpenCV model zoo). NDI® is a registered trademark of Vizrt NDI AB.</p>

<style>
  .kv div { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid #333; }
  .kv span:first-child { color: var(--dim); }
  .path { font-size: 12px; word-break: break-all; text-align: right; }
  a { color: var(--accent); }
  a.button { display: inline-block; background: var(--ctl); color: var(--text); border: 1px solid var(--line);
             border-radius: var(--radius); padding: 7px 12px; text-decoration: none; }
  .load { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
</style>

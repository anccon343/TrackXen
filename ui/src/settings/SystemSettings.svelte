<script lang="ts">
  import { onMount } from "svelte";
  import { access } from "../lib/access.svelte";
  import { faces } from "../lib/faces.svelte";
  import { live, postJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import { ui } from "../lib/ui.svelte";

  let newPin = $state("");
  let repeatPin = $state("");
  let currentPin = $state("");
  let pinMessage = $state("");
  let pin = $derived(access.state);
  onMount(() => access.check());

  async function savePin(off: boolean) {
    if (!off && newPin !== repeatPin) {
      pinMessage = "The two PINs are different.";
      return;
    }
    const error = await access.post("/api/access/pin", { pin: off ? null : newPin, current: currentPin,
                                                        ask_local: pin?.ask_local ?? false });
    pinMessage = error || (off ? "The PIN is off." : "PIN saved: other devices enter it once.");
    if (!error) newPin = repeatPin = currentPin = "";
  }

  async function signOutOthers() {
    const r = await postJSON<{ signed_out: number }>("/api/access/forget-devices", {});
    pinMessage = `${r.signed_out} device(s) signed out.`;
    await access.check();
  }

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
  {#if live.status?.gpu_libs && live.status.gpu_libs.state !== "not_needed"}
    {@const libs = live.status.gpu_libs}
    <div><span>NVIDIA libraries</span><span>{libs.state === "ready" ? "downloaded" : libs.state === "failed"
      ? `missing: ${libs.error}` : `downloading ${libs.done_mb ?? 0} of ${libs.total_mb ?? "…"} MB`}
      {#if libs.state === "failed"}<button onclick={() => live.request({ type: "gpu_libs_retry" })}>Download again</button>{/if}
    </span></div>
    <div><span>Libraries folder</span><span class="path">{libs.folder}</span></div>
  {/if}
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
<p class="dim">Loading replaces the setup and restarts the cameras. This PC keeps its own web address, performance mode and hardware test result.</p>

<h2 class="section">PIN for other devices</h2>
<p class="dim">When the PIN is on, phones, tablets and other PCs enter it once and then stay signed in. This PC
  doesn't need it (unless you tick "Ask on this PC too"). Stream Deck buttons send it in the header
  <code>X-TrackXen-PIN</code> (see the user guide).</p>
{#if pin}
  <div class="kv">
    <div><span>PIN</span><span>{pin.enabled ? `on, ${pin.devices} device(s) signed in` : "off"}</span></div>
  </div>
  <div class="pin">
    {#if pin.enabled && !pin.local}
      <label>Current PIN <input type="password" inputmode="numeric" autocomplete="off" bind:value={currentPin} /></label>
    {/if}
    <label>{pin.enabled ? "New PIN" : "PIN"}
      <input type="password" inputmode="numeric" maxlength="8" autocomplete="new-password" bind:value={newPin} /></label>
    <label>Repeat <input type="password" inputmode="numeric" maxlength="8" autocomplete="new-password" bind:value={repeatPin} /></label>
  </div>
  <div class="buttons">
    <button class="primary" disabled={!/^\d{4,8}$/.test(newPin)} onclick={() => savePin(false)}>
      {pin.enabled ? "Change the PIN" : "Switch the PIN on"}</button>
    {#if pin.enabled}
      <button onclick={() => savePin(true)}>Switch the PIN off</button>
      <button onclick={signOutOthers}>Sign out other devices</button>
    {/if}
  </div>
  {#if pin.enabled && pin.local}
    <label class="check"><input type="checkbox" checked={pin.ask_local}
      onchange={(e) => access.post("/api/access/ask-local", { on: e.currentTarget.checked })} /> Ask on this PC too</label>
  {/if}
  {#if pinMessage}<p>{pinMessage}</p>{/if}
  <p class="dim">4 to 8 digits. After 5 wrong tries a device has to wait 30 seconds, then longer each time.</p>
{/if}

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
  .pin { display: flex; gap: 12px; flex-wrap: wrap; margin: 10px 0; }
  .pin label { display: flex; flex-direction: column; gap: 4px; color: var(--dim); }
  .pin input { width: 9em; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .check { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
</style>

<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { updates } from "../lib/updates.svelte";

  // Settings → System → Updates: the latest version pushed to GitHub, downloaded, checked and installed.
  let token = $state("");
  let confirm = $state(false);
  let s = $derived(updates.status);
  let latest = $derived(s?.latest);
  let mb = (bytes: number) => (bytes / 1048576).toFixed(0);
  onMount(async () => {
    await updates.load();
    if (updates.status?.state === "downloading") updates.watch();
  });
  onDestroy(() => updates.unwatch());

  async function saveToken() {
    if (await updates.saveToken(token)) token = "";
  }
</script>

<h2 class="section">Updates</h2>
{#if s}
  <div class="kv">
    <div><span>This version</span><span>{s.current.version}{s.current.build ? ` (build ${s.current.build})` : " (not from a GitHub build)"}</span></div>
  </div>
  <p class="dim">Every push to main on GitHub builds a new setup program there (ready about half an hour later). Check for updates
    finds the latest one; Install closes TrackXen, stops the cameras, installs it and starts TrackXen again.</p>

  <div class="token">
    <label for="update-token">GitHub access token {s.token_set ? "(saved)" : "(needed: the repository is private)"}</label>
    <div class="row">
      <input id="update-token" type="password" autocomplete="off" bind:value={token}
             placeholder={s.token_set ? "saved: enter a new one to replace it" : "github_pat_…"} />
      <button disabled={updates.busy || !token.trim()} onclick={saveToken}>Save token</button>
      {#if s.token_set}<button disabled={updates.busy} onclick={() => updates.saveToken("")}>Remove</button>{/if}
    </div>
    <p class="dim small">On GitHub: Settings → Developer settings → Fine-grained tokens → Generate new token. Repository
      access: only TrackXen; Permissions: Contents, read-only. The token stays on this PC (not in settings or show files).</p>
  </div>

  <div class="buttons">
    <button class="primary" disabled={updates.busy || !s.token_set || s.state === "downloading"} onclick={() => updates.check()}>
      {s.state === "checking" ? "Checking…" : "Check for updates"}</button>
  </div>

  {#if latest}
    <div class="result">
      {#if s.newer}
        <p><b>New version: {latest.name}</b> (build {latest.build}{latest.published ? `, ${latest.published.slice(0, 10)}` : ""},
          {mb(latest.size)} MB)</p>
      {:else}
        <p>Up to date: the latest on GitHub is {latest.name} (build {latest.build}).</p>
      {/if}
      {#if latest.notes}<pre class="notes">{latest.notes}</pre>{/if}
      {#if s.newer}
        {#if s.state === "downloading"}
          <progress max={latest.size} value={s.received}></progress>
          <span class="dim">{mb(s.received)} / {mb(latest.size)} MB</span>
        {:else if s.state !== "ready"}
          <button disabled={updates.busy} onclick={() => updates.download()}>Download</button>
        {:else if !s.installed}
          <p class="dim">Downloaded and checked. Updates install only in the installed program, not in this development copy.</p>
        {:else if !confirm}
          <p class="dim">Downloaded and checked.</p>
          <button class="primary" onclick={() => (confirm = true)}>Install now…</button>
        {:else}
          <p class="warn">Not during a service: TrackXen closes, every camera stops, and it is back in about a minute.
            Windows may ask to allow the setup program.</p>
          <div class="buttons">
            <button class="danger" disabled={updates.busy} onclick={() => updates.install()}>Stop TrackXen and install</button>
            <button onclick={() => (confirm = false)}>Cancel</button>
          </div>
        {/if}
      {/if}
    </div>
  {/if}
  {#if s.error}<p class="error">{s.error}</p>{/if}
{:else}
  <p class="dim">Loading…</p>
{/if}
{#if updates.message}<p class="msg">{updates.message}</p>{/if}

<style>
  .kv div { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid #333; }
  .kv span:first-child { color: var(--dim); }
  .token { display: flex; flex-direction: column; gap: 4px; margin: 10px 0; }
  .token label { color: var(--dim); }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  .row input { flex: 1; min-width: 220px; max-width: 420px; }
  .small { font-size: 12px; margin: 0; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .result { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
  .result p { margin: 0; }
  .notes { white-space: pre-wrap; font-size: 12px; color: var(--dim); background: #111; border-radius: var(--radius);
           padding: 8px; max-height: 160px; overflow-y: auto; margin: 0; max-width: 100%; }
  progress { width: min(420px, 100%); }
  .warn { color: var(--yellow); }
  .error { color: #ff6b5b; }
  .msg { margin-top: 8px; }
</style>

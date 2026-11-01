<script lang="ts">
  import { onMount } from "svelte";
  import { access } from "../lib/access.svelte";

  let { onsignedin }: { onsignedin: () => void } = $props();
  let pin = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit() {
    if (pin.length < 4 || busy) return;
    busy = true;
    error = await access.signIn(pin);
    busy = false;
    pin = "";
    if (!error) onsignedin();
  }

  function press(key: string) {
    error = "";
    if (key === "back") pin = pin.slice(0, -1);
    else if (key === "ok") submit();
    else if (pin.length < 8) pin += key;
  }

  onMount(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^[0-9]$/.test(e.key)) press(e.key);
      else if (e.key === "Backspace") press("back");
      else if (e.key === "Enter") press("ok");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
</script>

<div class="screen">
  <div class="card">
    <h1>TrackXen</h1>
    <p class="dim">Enter the PIN to control the cameras from this device.</p>
    <div class="dots" aria-label="PIN, {pin.length} digits">
      {#each { length: Math.max(4, pin.length) } as _, i}<span class:on={i < pin.length}></span>{/each}
    </div>
    <p class="error" role="alert">{error}</p>
    <div class="pad">
      {#each ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as key}
        <button onclick={() => press(key)}>{key}</button>
      {/each}
      <button onclick={() => press("back")} aria-label="Delete">⌫</button>
      <button onclick={() => press("0")}>0</button>
      <button class="primary" disabled={pin.length < 4 || busy} onclick={() => press("ok")}>OK</button>
    </div>
    <p class="dim small">The device stays signed in. Ask whoever runs TrackXen for the PIN.</p>
  </div>
</div>

<style>
  .screen { height: 100%; display: grid; place-items: center; padding: 16px; }
  .card { width: min(320px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
          padding: 20px; text-align: center; }
  h1 { margin: 0 0 6px; color: var(--accent); font-size: 22px; }
  .dots { display: flex; justify-content: center; gap: 10px; margin: 18px 0 6px; }
  .dots span { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--dim); }
  .dots span.on { background: var(--accent); border-color: var(--accent); }
  .error { min-height: 1.3em; color: var(--red); margin: 4px 0 10px; }
  .pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .pad button { height: 52px; font-size: 20px; }
  .small { font-size: 12px; margin-top: 14px; }
</style>

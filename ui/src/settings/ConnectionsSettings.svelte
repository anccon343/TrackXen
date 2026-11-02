<script lang="ts">
  import { getJSON, live } from "../lib/live.svelte";
  import { ui } from "../lib/ui.svelte";

  // vMix (tally: which camera is live) and the MR18 / X Air mixer (audio-follow). Changes apply at
  // once and only restart these two links, never the cameras.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let cfg = $state<any>(null);
  let vmixHost = $state(""), vmixPort = $state(8099), mixerHost = $state(""), mixerPort = $state(10024);
  let mixers = $state<{ ip: string; name: string; model: string }[]>([]);
  let finding = $state(false);
  let vmix = $derived(live.status?.vmix ?? null);
  let mixer = $derived(live.status?.mixer ?? null);
  let cameras = $derived(live.status?.cameras ?? []);

  /** The saved settings, and (fields) the addresses in the form: not after choosing a vMix input,
   *  which would wipe addresses typed but not applied yet. */
  async function load(fields = true) {
    cfg = await getJSON("/api/config");
    if (!fields) return;
    vmixHost = cfg.vmix.host;
    vmixPort = cfg.vmix.port;
    mixerHost = cfg.mixer.host;
    mixerPort = cfg.mixer.port;
  }
  $effect(() => {
    load();
  });

  const isPort = (v: unknown) => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 65535;

  async function apply() {
    if (!isPort(vmixPort) || !isPort(mixerPort)) { // an emptied field would be sent as port 0
      ui.notify("The ports must be numbers from 1 to 65535");
      return;
    }
    const reply = await live.request({ type: "connections", vmix: { host: vmixHost.trim(), port: vmixPort },
                                       mixer: { host: mixerHost.trim(), port: mixerPort } });
    ui.notify(reply.ok ? "Connections applied" : reply.error ?? "Not possible");
    await load();
  }

  async function findMixers() {
    finding = true;
    try {
      mixers = (await getJSON<{ mixers: { ip: string; name: string; model: string }[] }>("/api/discover")).mixers;
      if (!mixers.length) ui.notify("No MR18 / X Air mixer answered on this network");
    } finally {
      finding = false;
    }
  }

  async function setInput(camera: string, input: string) {
    const reply = await live.request({ type: "vmix_input", camera, input });
    if (!reply.ok) ui.notify(reply.error ?? "Not possible");
    await load(false);
  }

  const inputOf = (camera: string): string => cfg?.cameras.find((c: { id: string }) => c.id === camera)?.vmix_input ?? "";
  const tallyText = ["", "Program", "Preview"];
</script>

{#if cfg}
  <h2 class="section">vMix</h2>
  <p class="dim">TrackXen reads vMix's tally to know which camera is live, and moves that camera calmly.
    Use 127.0.0.1 when vMix runs on this PC.</p>
  <div class="form">
    <label>vMix PC address <input type="text" bind:value={vmixHost} placeholder="for example 192.168.1.20" /></label>
    <label>Port <span class="range">1 - 65535</span><input type="number" bind:value={vmixPort} min="1" max="65535" /></label>
  </div>
  <p>Status: {#if !vmix}<span class="dim">off</span>{:else if vmix.connected}<span class="ok">connected, {vmix.inputs.length} inputs</span>
    {:else}<span class="bad">not connected{vmix.error ? `: ${vmix.error}` : ""}</span>{/if}</p>

  {#if vmix?.inputs.length}
    <h2 class="section">Camera inputs in vMix</h2>
    {#each cameras as cam (cam.id)}
      <div class="row">
        <span>{cam.name}</span>
        <select value={inputOf(cam.id)} onchange={(e) => setInput(cam.id, (e.currentTarget as HTMLSelectElement).value)}>
          <option value="">– none –</option>
          {#each vmix.inputs as inp (inp.key)}
            <option value={inp.key}>{inp.number}: {inp.title}{inp.tally ? ` (${tallyText[inp.tally]})` : ""}</option>
          {/each}
        </select>
        {#if vmix.suggested[cam.id] && vmix.suggested[cam.id] !== inputOf(cam.id)}
          <button class="small" onclick={() => setInput(cam.id, vmix!.suggested[cam.id])} title="The vMix input showing this camera's NDI source">Use suggestion</button>
        {/if}
      </div>
    {/each}
  {/if}

  <h2 class="section">Mixer (MR18 / X Air)</h2>
  <p class="dim">For audio-follow: TrackXen reads the input levels, channel names and mutes (nothing on the mixer is changed).</p>
  <div class="form">
    <label>Mixer address <input type="text" bind:value={mixerHost} placeholder="for example 192.168.1.30" /></label>
    <label>Port <span class="range">1 - 65535</span><input type="number" bind:value={mixerPort} min="1" max="65535" /></label>
  </div>
  <button onclick={findMixers} disabled={finding}>{finding ? "Searching…" : "Find mixers"}</button>
  {#each mixers as m (m.ip)}
    <button class="small" onclick={() => (mixerHost = m.ip)}>{m.name} ({m.model}) {m.ip}</button>
  {/each}
  <p>Status: {#if !mixer}<span class="dim">off</span>{:else if mixer.connected}<span class="ok">levels arriving</span>
    {:else}<span class="bad">no levels{mixer.error ? `: ${mixer.error}` : ""}</span>{/if}</p>

  <div class="apply"><button class="primary" onclick={apply}>Apply connections</button></div>
{:else}
  <p class="dim">Loading…</p>
{/if}

<style>
  .form { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 10px; margin-bottom: 8px; }
  .form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--dim); }
  .range { font-size: 10px; }
  .row { display: flex; align-items: center; gap: 10px; min-height: 40px; }
  .row > span { width: 160px; }
  .row select { flex: 1; min-width: 0; }
  .small { min-height: 28px; padding: 2px 10px; font-size: 12px; margin: 2px; }
  .ok { color: #3ddc6c; }
  .bad { color: #ff6b5b; }
  .apply { margin-top: 16px; }
  @media (max-width: 600px) {
    .form { grid-template-columns: 1fr; }
    .row { flex-wrap: wrap; }
    .row > span { width: 100%; }
  }
</style>

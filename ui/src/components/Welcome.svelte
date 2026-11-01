<script lang="ts">
  import { onMount } from "svelte";
  import { getJSON, live, postJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import { cameraTypes, describeTest } from "../lib/cameraTypes.svelte";
  import { ui } from "../lib/ui.svelte";

  // First start on a new PC (no cameras yet): check the PC, let the GPU engine get ready, find
  // the cameras on the network and add them. Everything here can be changed later in Settings.
  let { onsettings }: { onsettings: (page: string) => void } = $props();
  interface System { cpu: string; cpu_threads: number; memory_gb: number; engines: string[];
                     gpus: { name: string; memory_gb: number; driver: string }[] }
  interface Found { ndi: string; host: string; birddog: Record<string, string> | null }
  let system = $state<System | null>(null);
  let found = $state<Found[]>([]);
  let chosen = $state<Record<string, boolean>>({});
  let searching = $state(false);
  let searched = $state(false);
  let adding = $state(false);
  let test = $state<{ running: boolean; report: { recommendation: { mode: string; auto_preset: string | null }; summary: string } | null } | null>(null);
  let poller: ReturnType<typeof setInterval> | null = null;
  let status = $derived(live.status);
  let libs = $derived(status?.gpu_libs);
  let libsBusy = $derived(libs?.state === "checking" || libs?.state === "downloading");
  let libsPct = $derived(libs?.total_mb ? Math.round((100 * (libs.done_mb ?? 0)) / libs.total_mb) : 0);

  $effect(() => {
    getJSON<System>("/api/system").then((s) => (system = s));
    return () => { if (poller) clearInterval(poller); };
  });

  async function find() {
    searching = true;
    try {
      found = (await getJSON<{ ndi: Found[] }>("/api/discover")).ndi;
      chosen = Object.fromEntries(found.slice(0, 4).map((f) => [f.ndi, true]));
    } finally {
      searching = false;
      searched = true;
    }
  }

  // Cameras without NDI (PTZOptics): added by IP address, video from their own network stream
  let ipAddress = $state("");
  let ipType = $state("ptzoptics_g2");
  let byAddress = $state<{ host: string; model: string }[]>([]);
  let ipTest = $state<string[] | "running" | null>(null);
  let picked = $derived(found.filter((f) => chosen[f.ndi]).length + byAddress.length);
  onMount(() => cameraTypes.load());

  async function testAddress() {
    ipTest = "running";
    try {
      ipTest = describeTest(await cameraTypes.test(cameraTypes.create(ipType, 1, ipAddress.trim())));
    } catch (e) {
      ipTest = [`Test failed: ${e}`];
    }
  }

  function keepAddress() {
    byAddress = [...byAddress, { host: ipAddress.trim(), model: ipType }];
    ipAddress = "";
    ipTest = null;
  }

  async function addCameras() {
    const picks = found.filter((f) => chosen[f.ndi]);
    if (!picks.length && !byAddress.length) return;
    adding = true;
    try {
      await settings.load();
      type Pick = { model: string; host: string; ndi?: string; name?: string };
      const ndi: Pick[] = picks.map((f) => ({ model: f.birddog ? "birddog_p400" : "other", host: f.host, ndi: f.ndi,
                                              name: f.birddog?.HostName }));
      settings.draft!.cameras = [...ndi, ...byAddress].slice(0, 4).map((c: Pick, i) => {
        const cam = cameraTypes.create(c.model, i + 1, c.host);
        if (c.ndi) Object.assign(cam, { ndi: c.ndi, video: "ndi", name: c.name || cam.name });
        return cam;
      });
      await settings.apply();
      ui.notify(`${settings.draft!.cameras.length} camera(s) added`);
    } finally {
      adding = false;
    }
  }

  async function hardwareTest() {
    await postJSON("/api/hwtest", { cameras: Math.max(1, Object.values(chosen).filter(Boolean).length || 4), fps: 30,
                                    shared_with_vmix: false, quick: true });
    test = { running: true, report: null };
    poller = setInterval(async () => {
      test = await getJSON("/api/hwtest");
      if (!test?.running && poller) { clearInterval(poller); poller = null; }
    }, 1000);
  }

  async function useRecommendation() {
    const rec = test?.report?.recommendation;
    if (!rec || rec.mode === "insufficient") return;
    await settings.load();
    settings.draft!.performance_mode = rec.mode;
    if (rec.auto_preset) settings.draft!.auto_preset = rec.auto_preset;
    await settings.apply();
    ui.notify(`Performance mode: ${rec.mode.toUpperCase()}`);
  }

  const engineText: Record<string, string> = { tensorrt: "TensorRT (fastest)", cuda: "CUDA", directml: "DirectML", cpu: "CPU only (slow)" };
</script>

<div class="welcome">
  <h1>Welcome to Track<b>Xen</b></h1>
  <p class="dim">Three steps to get started. Everything can be changed later in Settings.</p>

  <section>
    <h2><span class="n">1</span> This PC</h2>
    {#if system}
      <div class="kv">
        <div><span>Graphics</span><span>{system.gpus.map((g) => `${g.name} (${g.memory_gb} GB, driver ${g.driver})`).join(", ") || "no NVIDIA graphics card found"}</span></div>
        <div><span>Processor / memory</span><span>{system.cpu_threads} threads, {system.memory_gb} GB</span></div>
        <div><span>Best engine</span><span>{engineText[system.engines[0]] ?? system.engines[0] ?? "–"}</span></div>
        <div><span>Now running on</span><span>{status?.engine ? engineText[status.engine] ?? status.engine : "starting…"}</span></div>
      </div>
      {#if libsBusy}
        <div class="building">
          <div class="bar done"><span style="width: {libsPct}%"></span></div>
          <p>Downloading NVIDIA's graphics libraries: {libs?.done_mb ?? 0} of {libs?.total_mb || "…"} MB ({libsPct} %).
            Only on the first start; TrackXen follows people on the processor until then, slowly.</p>
        </div>
      {:else if libs?.state === "failed"}
        <div class="building">
          <p class="warn">NVIDIA's graphics libraries couldn't be downloaded: {libs.error}. TrackXen tries again every minute.</p>
          <button onclick={() => live.request({ type: "gpu_libs_retry" })}>Download again now</button>
        </div>
      {/if}
      {#if status?.engine_building}
        <div class="building">
          <div class="bar"><span></span></div>
          <p>Preparing the GPU engine for this graphics card ({status.engine_building_s} s). Only on the first start; it
            usually takes 2–5 minutes. TrackXen already works meanwhile, a little slower.</p>
        </div>
      {/if}
      {#if !system.gpus.length}
        <p class="warn">Without an NVIDIA graphics card TrackXen runs on the CPU and can only follow people slowly.</p>
      {/if}
      <div class="row">
        <button onclick={hardwareTest} disabled={test?.running || libsBusy}>{test?.running ? "Testing…" : libsBusy
          ? "Test this PC's speed (after the download)" : "Test this PC's speed (about a minute)"}</button>
        {#if test?.report}
          <span>Recommended: <b>{test.report.recommendation.mode.toUpperCase()}</b>{test.report.recommendation.auto_preset ? ` (${test.report.recommendation.auto_preset})` : ""}</span>
          <button class="primary" onclick={useRecommendation} disabled={test.report.recommendation.mode === "insufficient"}>Use it</button>
        {/if}
      </div>
    {:else}
      <p class="dim">Checking…</p>
    {/if}
  </section>

  <section>
    <h2><span class="n">2</span> Cameras</h2>
    <p class="dim">The PC and the cameras must be on the same network, with NDI on in each camera (BirdUI).</p>
    <button onclick={find} disabled={searching}>{searching ? "Searching the network…" : "Find cameras"}</button>
    {#if searched && !found.length}<p class="warn">No NDI sources found. Check the network, or add cameras by hand in Settings → Cameras.</p>{/if}
    {#each found as f (f.ndi)}
      <label class="found">
        <input type="checkbox" bind:checked={chosen[f.ndi]} />
        <span><b>{f.ndi}</b><br /><span class="dim">{f.host || "address unknown"}{f.birddog ? ` · BirdDog ${f.birddog.FirmwareVersion ?? ""}` : ""}</span></span>
      </label>
    {/each}
    <h3>Cameras without NDI (PTZOptics)</h3>
    <p class="dim">Added by their IP address; TrackXen takes their video from the camera's own network stream.</p>
    <div class="row">
      <input type="text" bind:value={ipAddress} placeholder="Camera IP, e.g. 192.168.1.60" />
      <select bind:value={ipType}>
        {#each Object.entries(cameraTypes.list) as [key, t]}<option value={key}>{t.name}</option>{/each}
      </select>
      <button onclick={testAddress} disabled={!ipAddress.trim() || ipTest === "running"}>{ipTest === "running" ? "Testing…" : "Test"}</button>
      <button onclick={keepAddress} disabled={!ipAddress.trim() || picked >= 4}>Add to the list</button>
    </div>
    {#if Array.isArray(ipTest)}<div class="dim">{#each ipTest as line}<div>{line}</div>{/each}</div>{/if}
    {#each byAddress as c, i (i)}
      <div class="found"><span><b>{c.host}</b> · {cameraTypes.list[c.model]?.name ?? c.model}</span>
        <button onclick={() => (byAddress = byAddress.filter((_, j) => j !== i))}>Remove</button></div>
    {/each}
    {#if picked}
      <button class="primary" onclick={addCameras} disabled={adding}>{adding ? "Adding…" : `Add ${Math.min(4, picked)} camera(s)`}</button>
      <p class="dim">Up to 4. For exact positions set each P400's FreeD output to this PC (BirdUI → CamControl → FreeD);
        Settings → Cameras shows the ports.</p>
    {/if}
  </section>

  <section>
    <h2><span class="n">3</span> vMix and the mixer (optional)</h2>
    <p class="dim">vMix tally lets cameras move calmly while live; the MR18 / X Air lets a microphone send a camera to its speaker.</p>
    <button onclick={() => onsettings("connections")}>Set up vMix &amp; mixer</button>
  </section>
</div>

<style>
  .welcome { max-width: 860px; margin: 0 auto; padding: 24px 20px; overflow-y: auto; height: 100%; }
  h1 { font-size: 26px; font-weight: 400; margin: 0 0 4px; }
  h1 b { color: var(--accent); }
  section { background: var(--panel); border-radius: var(--radius); padding: 14px 16px; margin-top: 14px; }
  h2 { font-size: 16px; margin: 0 0 10px; display: flex; align-items: center; gap: 10px; }
  .n { width: 26px; height: 26px; border-radius: 50%; background: var(--accent); display: grid; place-items: center; font-size: 13px; }
  .kv div { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; border-bottom: 1px solid #333; font-size: 13px; }
  .kv span:first-child { color: var(--dim); }
  .kv span:last-child { text-align: right; }
  .row { display: flex; align-items: center; gap: 12px; margin-top: 10px; flex-wrap: wrap; }
  .building { margin-top: 10px; }
  .building p { font-size: 13px; margin: 6px 0 0; }
  .bar { height: 6px; background: #111; border-radius: 3px; overflow: hidden; position: relative; }
  .bar span { position: absolute; top: 0; bottom: 0; width: 30%; background: var(--accent); animation: slide 1.6s ease-in-out infinite; }
  .bar.done span { left: 0; animation: none; transition: width 0.5s; }
  @keyframes slide { from { left: -30%; } to { left: 100%; } }
  .found { display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; border-bottom: 1px solid #333; }
  .found input { min-height: 0; margin-top: 4px; accent-color: var(--accent); }
  .warn { color: var(--yellow); font-size: 13px; }
  h3 { font-size: 14px; margin: 16px 0 4px; }
  .row input { flex: 1; min-width: 180px; }
  .found { justify-content: space-between; align-items: center; }
  section > button { margin-top: 8px; }
</style>

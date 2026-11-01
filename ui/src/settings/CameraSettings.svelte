<script lang="ts">
  import { getJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import Field from "../ui/Field.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Cameras: which NDI source and which camera address each one uses. "Find devices" lists what is
  // on the network (NDI sources, and the BirdDog camera behind each one) so nothing is typed by hand.
  type Found = { ndi: string; host: string; birddog: Record<string, string> | null };
  let found = $state<Found[]>([]);
  let searching = $state(false);
  let message = $state("");
  let cams = $derived(settings.draft?.cameras ?? []);

  async function discover() {
    searching = true;
    message = "";
    try {
      const r = await getJSON<{ ndi: Found[] }>("/api/discover");
      found = r.ndi;
      message = found.length ? `${found.length} NDI source(s) found.` : "No NDI sources found on this network.";
    } catch (e) {
      message = `Search failed: ${e}`;
    } finally {
      searching = false;
    }
  }

  let devices = $state<{ index: number; width: number; height: number }[]>([]);
  let probing = $state(false);

  async function findCapture() {
    probing = true;
    try {
      devices = await getJSON("/api/capture-devices");
      message = devices.length ? `${devices.length} capture device(s) found.` : "No capture devices found (or all are in use).";
    } finally {
      probing = false;
    }
  }

  function add() {
    const n = cams.length + 1;
    settings.draft!.cameras = [...cams, {
      id: `cam${n}`, name: `Camera ${n}`, video: "ndi", capture_device: 0, ndi: "", host: "", visca_port: 52381, freed_port: 40000 + n,
      video_delay: 0.08, low_bandwidth: false,
    }];
  }

  function remove(i: number) {
    settings.draft!.cameras = cams.filter((_: unknown, j: number) => j !== i);
  }

  function pick(i: number, ndi: string) {
    const c = settings.draft!.cameras[i];
    c.ndi = ndi;
    const hit = found.find((f) => f.ndi === ndi);
    if (hit?.host && !c.host) c.host = hit.host; // the camera's IP for VISCA
  }
</script>

<p class="dim">Each camera needs its NDI video source and its IP address for control (VISCA). For exact positions,
  set the camera's FreeD output (BirdUI → CamControl → FreeD) to this PC's IP and the FreeD port below.</p>
<div class="actions">
  <button onclick={discover} disabled={searching}>{searching ? "Searching…" : "Find devices"}</button>
  <button onclick={add} disabled={cams.length >= 4}>Add camera</button>
  {#if cams.some((c: { video?: string }) => c.video === "capture")}
    <button onclick={findCapture} disabled={probing} title="Opens each capture input for a moment">{probing ? "Looking…" : "Find capture devices"}</button>
  {/if}
  <span class="dim">{message}</span>
</div>

{#each cams as cam, i (i)}
  <h2 class="section">Camera {i + 1}</h2>
  <Field label="Name" changed={settings.changed((c) => c.cameras[i]?.name)}>
    <input type="text" bind:value={cam.name} />
  </Field>
  <Field label="Video from" help="Control always goes over the network (VISCA)" changed={settings.changed((c) => c.cameras[i]?.video)}>
    <select bind:value={cam.video}>
      <option value="ndi">NDI (network)</option>
      <option value="capture">HDMI / SDI capture card</option>
    </select>
  </Field>
  {#if (cam.video ?? "ndi") === "ndi"}
    <Field label="NDI source" help="Part of the name is enough, e.g. BIRDDOG-12345" changed={settings.changed((c) => c.cameras[i]?.ndi)}>
      <input type="text" bind:value={cam.ndi} list="ndi-sources" placeholder="NDI source name" />
      {#if found.length}
        <select onchange={(e) => pick(i, (e.currentTarget as HTMLSelectElement).value)}>
          <option value="">Choose a found source…</option>
          {#each found as f}<option value={f.ndi}>{f.ndi}{f.birddog ? " (BirdDog)" : ""}</option>{/each}
        </select>
      {/if}
    </Field>
  {:else}
    <Field label="Capture device" range="0 - 15" help="Number of the capture input on this PC (Find capture devices lists them). A card used by vMix on this PC can't be shared: take that camera from vMix as NDI instead."
           changed={settings.changed((c) => c.cameras[i]?.capture_device)}>
      <input type="number" min="0" max="15" bind:value={cam.capture_device} />
      {#if devices.length}
        <select onchange={(e) => (cam.capture_device = +(e.currentTarget as HTMLSelectElement).value)}>
          <option value="">Choose a found device…</option>
          {#each devices as d}<option value={d.index}>Device {d.index} ({d.width}×{d.height})</option>{/each}
        </select>
      {/if}
    </Field>
  {/if}
  <Field label="Camera IP address" help="For VISCA control" changed={settings.changed((c) => c.cameras[i]?.host)}>
    <input type="text" bind:value={cam.host} placeholder="e.g. 192.168.1.50" />
  </Field>
  <Field label="VISCA port" range="1 - 65535" changed={settings.changed((c) => c.cameras[i]?.visca_port)}>
    <input type="number" min="1" max="65535" bind:value={cam.visca_port} />
  </Field>
  <Field label="FreeD port on this PC" range="1024 - 65535" help="Leave empty without FreeD (positions are then polled over VISCA)"
         changed={settings.changed((c) => c.cameras[i]?.freed_port)}>
    <input type="number" min="1024" max="65535" bind:value={cam.freed_port} />
  </Field>
  <Field label="Video delay (s)" range="0 - 1" help="Measured by calibration; 0.08 until then"
         changed={settings.changed((c) => c.cameras[i]?.video_delay)}>
    <input type="number" min="0" max="1" step="0.01" bind:value={cam.video_delay} />
  </Field>
  <Field label="NDI low bandwidth" help="Lighter on the network; slightly less detail for detection"
         changed={settings.changed((c) => c.cameras[i]?.low_bandwidth)}>
    <Toggle checked={cam.low_bandwidth} onchange={(v) => (cam.low_bandwidth = v)} />
  </Field>
  <div class="remove"><button onclick={() => remove(i)}>Remove camera {i + 1}</button></div>
{:else}
  <p>No cameras yet. Press "Add camera" (and "Find devices" to fill in the details).</p>
{/each}

<datalist id="ndi-sources">{#each found as f}<option value={f.ndi}></option>{/each}</datalist>

<style>
  .actions { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
  .remove { display: flex; justify-content: flex-end; }
</style>

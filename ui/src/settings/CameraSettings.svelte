<script lang="ts">
  import { onMount } from "svelte";
  import { getJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import { cameraTypes, describeTest, VISCA_CONNECTIONS } from "../lib/cameraTypes.svelte";
  import Field from "../ui/Field.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Cameras: the type (BirdDog P400, PTZOptics G2, other), where the video comes from (NDI, the camera's own
  // network stream, or a capture card) and how it is controlled (VISCA). "Find devices" lists the NDI sources
  // on the network; cameras without NDI are added by their IP address.
  type Found = { ndi: string; host: string; birddog: Record<string, string> | null };
  let found = $state<Found[]>([]);
  let searching = $state(false);
  let message = $state("");
  let tests = $state<Record<number, string[] | "running">>({});
  let cams = $derived(settings.draft?.cameras ?? []);
  onMount(() => cameraTypes.load());

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
    settings.draft!.cameras = [...cams, cameraTypes.add("birddog_p400", cams)];
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

  async function test(i: number) {
    tests[i] = "running";
    try {
      tests[i] = describeTest(await cameraTypes.test(cams[i]));
    } catch (e) {
      tests[i] = [`Test failed: ${e}`];
    }
  }
</script>

<p class="dim">Each camera needs its video (NDI, its own network stream, or a capture card) and its IP address for
  control (VISCA). BirdDog P400: set its FreeD output (BirdUI → CamControl → FreeD) to this PC's IP and the FreeD
  port below. PTZOptics: pick its type; it streams over the network and has no FreeD.</p>
<div class="actions">
  <button onclick={discover} disabled={searching}>{searching ? "Searching…" : "Find devices"}</button>
  <button onclick={add} disabled={cams.length >= 4}>Add camera</button>
  {#if cams.some((c: { video?: string }) => c.video === "capture")}
    <button onclick={findCapture} disabled={probing} title="Opens each capture input for a moment">{probing ? "Looking…" : "Find capture devices"}</button>
  {/if}
  <span class="dim">{message}</span>
</div>

{#each cams as cam, i (i)}
  {@const type = cameraTypes.list[cam.model ?? "birddog_p400"]}
  <h2 class="section">Camera {i + 1}</h2>
  <Field label="Name" changed={settings.changed((c) => c.cameras[i]?.name)}>
    <input type="text" bind:value={cam.name} />
  </Field>
  <Field label="Camera type" help={type?.about ?? ""} changed={settings.changed((c) => c.cameras[i]?.model)}>
    <select value={cam.model ?? "birddog_p400"} onchange={(e) => cameraTypes.apply(cam, (e.currentTarget as HTMLSelectElement).value, cams)}>
      {#each Object.entries(cameraTypes.list) as [key, t]}<option value={key}>{t.name}</option>{/each}
    </select>
  </Field>
  <Field label="Camera IP address" help="For VISCA control (and the network stream)" changed={settings.changed((c) => c.cameras[i]?.host)}>
    <input type="text" bind:value={cam.host} placeholder="e.g. 192.168.1.50" />
  </Field>
  <Field label="Video from" help="Control always goes over the network (VISCA)" changed={settings.changed((c) => c.cameras[i]?.video)}>
    <select bind:value={cam.video}>
      <option value="ndi">NDI (network)</option>
      <option value="stream">The camera's network stream (RTSP)</option>
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
    <Field label="NDI low bandwidth" help="Lighter on the network; slightly less detail for detection"
           changed={settings.changed((c) => c.cameras[i]?.low_bandwidth)}>
      <Toggle checked={cam.low_bandwidth} onchange={(v) => (cam.low_bandwidth = v)} />
    </Field>
  {:else if cam.video === "stream"}
    <Field label="Stream address" help={`Empty: ${cameraTypes.defaultUrl(cam) || "rtsp://<camera IP>:554/1"} (PTZOptics main stream; /2 is the sub stream). With a camera login: rtsp://user:password@…`}
           changed={settings.changed((c) => c.cameras[i]?.stream_url)}>
      <input type="text" bind:value={cam.stream_url} placeholder={cameraTypes.defaultUrl(cam) || "rtsp://192.168.1.60:554/1"} />
      <button onclick={() => (cam.stream_url = cameraTypes.defaultUrl(cam))} disabled={!cam.host}>Main stream</button>
      <button onclick={() => (cam.stream_url = cameraTypes.defaultUrl(cam, true))} disabled={!cam.host}>Sub stream</button>
    </Field>
    <Field label="Stream transport" help="TCP: no damaged pictures on a busy network. UDP: can be a little quicker."
           changed={settings.changed((c) => c.cameras[i]?.stream_transport)}>
      <select bind:value={cam.stream_transport}><option value="tcp">TCP</option><option value="udp">UDP</option></select>
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
  <Field label="VISCA connection" help="PTZOptics: UDP works next to vMix or a joystick (the camera takes only one TCP connection)"
         changed={settings.changed((c) => c.cameras[i]?.visca_transport)}>
    <select value={cam.visca_transport ?? "sony"}
            onchange={(e) => { cam.visca_transport = (e.currentTarget as HTMLSelectElement).value; cam.visca_port = VISCA_CONNECTIONS[cam.visca_transport].port; }}>
      {#each Object.entries(VISCA_CONNECTIONS) as [key, v]}<option value={key}>{v.label}</option>{/each}
    </select>
  </Field>
  <Field label="VISCA port" range="1 - 65535" changed={settings.changed((c) => c.cameras[i]?.visca_port)}>
    <input type="number" min="1" max="65535" bind:value={cam.visca_port} />
  </Field>
  {#if cam.model !== "ptzoptics_g2"}
    <Field label="FreeD port on this PC" range="1024 - 65535" help="Leave empty without FreeD (positions are then asked over VISCA)"
           changed={settings.changed((c) => c.cameras[i]?.freed_port)}>
      <input type="number" min="1024" max="65535" bind:value={cam.freed_port} />
    </Field>
  {/if}
  <Field label="Video delay (s)" range="0 - 1" help={cam.video === "stream" ? "Network streams are slower than NDI: about 0.2 until measured (tools/stream_probe.py)" : "Measured by calibration; 0.08 until then"}
         changed={settings.changed((c) => c.cameras[i]?.video_delay)}>
    <input type="number" min="0" max="1" step="0.01" bind:value={cam.video_delay} />
  </Field>
  {#if type?.estimate?.length}
    <p class="dim small">From the data sheet until measured on this camera (lab tools): {type.estimate.join(", ").replaceAll("_", " ")}.</p>
  {/if}
  <div class="row">
    <button onclick={() => test(i)} disabled={!cam.host || tests[i] === "running"}>{tests[i] === "running" ? "Testing…" : "Test camera"}</button>
    {#if Array.isArray(tests[i])}<div class="result">{#each tests[i] as line}<div>{line}</div>{/each}</div>{/if}
    <span class="grow"></span>
    <button onclick={() => remove(i)}>Remove camera {i + 1}</button>
  </div>
{:else}
  <p>No cameras yet. Press "Add camera" (and "Find devices" to fill in the details).</p>
{/each}

<datalist id="ndi-sources">{#each found as f}<option value={f.ndi}></option>{/each}</datalist>

<style>
  .actions { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
  .row { display: flex; gap: 10px; align-items: flex-start; margin-top: 8px; flex-wrap: wrap; }
  .result { font-size: 13px; }
  .grow { flex: 1; }
  .small { font-size: 12px; margin: 4px 0 0; }
</style>

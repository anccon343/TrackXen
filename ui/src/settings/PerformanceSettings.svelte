<script lang="ts">
  import { getJSON, live, postJSON } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import Field from "../ui/Field.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Performance mode (Light / Auto / HYPER) and the hardware test that recommends one.
  type Preset = { name: string; model: string; imgsz: number; detect_fps: number; description: string };
  type HwTest = { running: boolean; lines: string[]; report: { recommendation: { mode: string; auto_preset: string | null }; summary: string; text_file: string } | null };
  let presets = $state<Record<string, Preset>>({});
  let test = $state<HwTest>({ running: false, lines: [], report: null });
  let cameras = $state(Math.max(1, settings.draft?.cameras?.length || 4));
  let fps = $state(30);
  let shared = $state(false);
  let quick = $state(false);
  let poller: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    getJSON<Record<string, Preset>>("/api/presets").then((p) => (presets = p));
    getJSON<HwTest>("/api/hwtest").then((t) => { test = t; if (t.running) poll(); });
    return () => { if (poller) clearInterval(poller); };
  });

  function poll() {
    if (poller) return;
    poller = setInterval(async () => {
      test = await getJSON<HwTest>("/api/hwtest");
      if (!test.running && poller) { clearInterval(poller); poller = null; }
    }, 1000);
  }

  async function run() {
    await postJSON("/api/hwtest", { cameras, fps, shared_with_vmix: shared, quick });
    test = { running: true, lines: [], report: null };
    poll();
  }

  function useRecommendation() {
    const rec = test.report!.recommendation;
    if (rec.mode === "insufficient") return;
    settings.draft!.performance_mode = rec.mode;
    if (rec.auto_preset) settings.draft!.auto_preset = rec.auto_preset;
  }

  const modes = [
    { id: "light", title: "Light", text: "For slower PCs, or when vMix shares this PC. Smallest model, fewer detections." },
    { id: "auto", title: "Auto", text: "Uses the setting the hardware test found right for this PC." },
    { id: "hyper", title: "HYPER", text: "Best and most responsive tracking whatever the load: largest model, high resolution, every frame." },
  ];
</script>

<h2 class="section">Mode</h2>
<div class="modes">
  {#each modes as m}
    <button class="mode" class:on={settings.draft?.performance_mode === m.id} onclick={() => (settings.draft!.performance_mode = m.id)}>
      <strong>{m.title}</strong><span>{m.text}</span>
    </button>
  {/each}
</div>
{#if settings.draft?.performance_mode === "auto"}
  <Field label="Auto runs" help="Set by the hardware test; change it by hand only if you know why" changed={settings.changed((c) => c.auto_preset)}>
    <select bind:value={settings.draft!.auto_preset}>
      {#each Object.values(presets).filter((p) => p.name !== "hyper") as p}<option value={p.name}>{p.name}: {p.description}</option>{/each}
    </select>
  </Field>
{/if}
<p class="dim">Running now: {live.status ? `${live.status.performance?.mode ?? "?"} (${live.status.performance?.preset ?? "?"}: ${live.status.performance?.model ?? ""} @ ${live.status.performance?.imgsz ?? ""})` : "–"}. Changes take effect with Apply.</p>

<h2 class="section">Hardware test</h2>
<p class="dim">Measures this PC with the real models and recommends a mode. Stop tracking first: running cameras
  share the graphics card and make the numbers lower. The first test builds TensorRT engines (a few minutes per model).</p>
<Field label="Cameras" range="1 - 4"><input type="number" min="1" max="4" bind:value={cameras} /></Field>
<Field label="Camera frame rate" range="fps"><input type="number" min="10" max="60" bind:value={fps} /></Field>
<Field label="vMix runs on this PC" help="Asks for more headroom"><Toggle checked={shared} onchange={(v) => (shared = v)} /></Field>
<Field label="Quick test" help="Skips TensorRT: quicker, but the numbers come out too low"><Toggle checked={quick} onchange={(v) => (quick = v)} /></Field>
<div class="actions">
  <button class="accent" onclick={run} disabled={test.running}>{test.running ? "Testing…" : "Run hardware test"}</button>
  {#if test.report && !test.running && test.report.recommendation.mode !== "insufficient"}
    <button onclick={useRecommendation}>Use this recommendation</button>
  {/if}
</div>
{#if test.lines.length}<pre>{test.lines.join("\n")}</pre>{/if}
{#if test.report?.text_file}<p class="dim">Report saved: {test.report.text_file}</p>{/if}

<style>
  .modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
  .mode { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left; padding: 10px 12px; min-height: 80px; }
  .mode span { font-size: 12px; color: var(--dim); }
  .mode.on span { color: #fff; }
  .actions { display: flex; gap: 8px; margin: 8px 0; }
  pre { background: #111; border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; font-size: 12px; white-space: pre-wrap; }
</style>

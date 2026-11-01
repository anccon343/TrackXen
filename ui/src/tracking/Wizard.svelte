<script lang="ts">
  import { onMount } from "svelte";
  import { live, type AreaKind, type CameraStatus } from "../lib/live.svelte";
  import { slots } from "../lib/slots.svelte";
  import { MOUSE_MODES, ui, type MouseMode } from "../lib/ui.svelte";
  import VideoCanvas from "../components/VideoCanvas.svelte";
  import PtzSliders from "./PtzSliders.svelte";
  import Steps from "./Steps.svelte";

  // Canon's Settings Wizard (Previous / Next through steps 1-10) and its individual settings
  // pages (pick any step, then Finish). Every page shows the picture with pan/tilt/zoom sliders.
  // The wizard saves on every Previous/Next; Finish saves and starts Auto Tracking.
  let { camera, mode, onclose }: { camera: CameraStatus; mode: "wizard" | "pages"; onclose: () => void } = $props();

  const STEPS: { title: string; mouse: MouseMode; shown: AreaKind[] | null; editable: AreaKind[] }[] = [
    { title: "Pan/Tilt Operation Control", mouse: "center", shown: ["range"], editable: [] },
    { title: "Initial Position", mouse: "center", shown: ["range", "initial"], editable: [] },
    { title: "Priority Display Area / Fixed Viewing Angle Area", mouse: "area", shown: ["initial", "priority", "fixed"], editable: ["priority", "fixed"] },
    { title: "Tracking Target Auto Select", mouse: "area", shown: ["initial", "auto_select", "exclusion"], editable: ["auto_select"] },
    { title: "Composition", mouse: "silhouette", shown: [], editable: [] },
    { title: "Auto Select Exclusion Area", mouse: "area", shown: ["auto_select", "exclusion"], editable: ["exclusion"] },
    { title: "Tracking Halting Area", mouse: "area", shown: ["halting"], editable: ["halting"] },
    { title: "Auto Framing", mouse: "select", shown: null, editable: [] },
    { title: "Registered Face Tracking", mouse: "select", shown: null, editable: [] },
    { title: "Camera Preset Link", mouse: "select", shown: null, editable: [] },
  ];
  let step = $state(1);
  let info = $derived(STEPS[step - 1]);
  let slot = $derived(slots.camera === camera.id ? slots.slot : undefined);
  let name = $state("");
  let editingName = false;

  $effect(() => slots.sync(camera));
  $effect(() => {
    if (slot && !editingName) name = slot.name;
  });
  $effect(() => {
    ui.mouseMode = STEPS[step - 1].mouse; // each page starts in the mouse mode it needs
  });
  onMount(() => {
    if (camera.tracking) {
      live.send({ type: "tracking", camera: camera.id, on: false });
      ui.notify("Auto tracking is off while you change the settings.");
    }
    return () => (ui.mouseMode = "select");
  });

  async function go(to: number) {
    if (mode === "wizard") await slots.command({ type: "save" }, false); // Canon: each page is saved on Previous/Next
    step = to;
  }

  async function finish() {
    await slots.command({ type: "save" }, false);
    live.send({ type: "tracking", camera: camera.id, on: true });
    onclose();
  }

  function saveName() {
    editingName = false;
    if (slot && name !== slot.name) slots.patch({ name: name.slice(0, 40) });
  }
</script>

<div class="wizard">
  <header>
    <span class="cam">{camera.name}</span>
    <div class="segments" role="group" aria-label="App Settings">
      {#each [0, 1, 2, 3, 4] as i}
        <button class:on={camera.slot === i} onclick={() => slots.command({ type: "slot", index: i })}>{i + 1}</button>
      {/each}
    </div>
    <input type="text" maxlength="40" bind:value={name} aria-label="App Settings name" placeholder="Name"
           onfocus={() => (editingName = true)} onblur={saveName} onkeydown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()} />
    <span class="grow"></span>
    <button onclick={onclose}>Close</button>
  </header>

  <nav aria-label="Settings pages">
    {#each STEPS as s, i}
      <button class:active={step === i + 1} onclick={() => go(i + 1)} title={s.title}>
        <span class="num">{i + 1}</span><span class="title">{s.title}</span>
      </button>
    {/each}
  </nav>

  <section class="panel">
    <h1>{step}. {info.title}</h1>
    {#if slots.problems.length}
      {#each slots.problems as p}<p class="warn">{p}</p>{/each}
    {/if}
    {#if slot}<Steps {step} {slot} {camera} />{:else}<p class="dim">Loading…</p>{/if}
  </section>

  <section class="picture">
    <div class="tools">
      <select bind:value={ui.mouseMode} aria-label="Mouse operation">
        {#each MOUSE_MODES as m}<option value={m.id}>{m.label}</option>{/each}
      </select>
      <span class="grow"></span>
      <button disabled={!slot?.initial.set} onclick={() => slots.command({ type: "view", what: "initial" }, false)}>View Initial Position</button>
    </div>
    <PtzSliders {camera}>
      <VideoCanvas {camera} shown={info.shown} editable={info.editable} />
    </PtzSliders>
  </section>

  <footer>
    {#if mode === "wizard"}
      <button disabled={step === 1} onclick={() => go(step - 1)}>Previous</button>
      <span class="mono">{step}/{STEPS.length}</span>
      {#if step < STEPS.length}<button class="accent" onclick={() => go(step + 1)}>Next</button>
      {:else}<button class="primary" onclick={finish}>Finish</button>{/if}
    {:else}
      <span class="dim">Changes work at once. Finish saves them and starts Auto Tracking.</span>
      <button class="primary" onclick={finish}>Finish</button>
    {/if}
  </footer>
</div>

<style>
  .wizard { display: grid; height: 100%; min-height: 0; grid-template-columns: 220px minmax(280px, 380px) minmax(0, 1fr);
            grid-template-rows: auto minmax(0, 1fr) auto; }
  header { grid-column: 1 / -1; display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: var(--panel-2);
           border-bottom: 1px solid var(--line); }
  .cam { font-weight: 700; }
  .segments { display: flex; gap: 3px; }
  .segments button { width: 36px; padding: 0; font-weight: 700; }
  header input { width: 240px; }
  .grow { flex: 1; }
  nav { background: var(--panel-2); overflow-y: auto; display: flex; flex-direction: column; padding: 6px 0; }
  nav button { display: flex; gap: 8px; align-items: center; text-align: left; background: none; border: none;
               border-left: 4px solid transparent; border-radius: 0; padding: 8px 10px; min-height: var(--touch); }
  nav button.active { border-left-color: var(--accent); background: #2f2f2f; }
  nav .num { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 50%; background: #444;
             font-size: 12px; font-weight: 700; flex: none; }
  nav button.active .num { background: var(--accent); }
  nav .title { font-size: 13px; }
  .panel { padding: 12px 16px; overflow-y: auto; background: var(--panel); }
  h1 { font-size: 16px; margin: 0 0 10px; }
  .warn { font-size: 12px; color: var(--yellow); margin: 0 0 6px; }
  .picture { padding: 10px 12px; overflow-y: auto; min-width: 0; }
  .tools { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
  footer { grid-column: 1 / -1; display: flex; justify-content: center; align-items: center; gap: 16px; padding: 8px;
           border-top: 1px solid var(--line); background: var(--panel-2); }
  footer button { min-width: 120px; }
  @media (max-width: 1100px) {
    .wizard { grid-template-columns: 56px minmax(260px, 340px) minmax(0, 1fr); }
    nav .title { display: none; }
  }
  @media (max-width: 760px) {
    .wizard { grid-template-columns: minmax(0, 1fr); grid-template-rows: none; grid-auto-rows: max-content; overflow-y: auto; }
    header { order: 0; flex-wrap: wrap; }
    .picture { order: 1; overflow: visible; }
    nav { order: 2; flex-direction: row; overflow-x: auto; padding: 0; }
    .panel { order: 3; overflow: visible; }
    footer { order: 4; position: sticky; bottom: 0; }
    nav button { border-left: none; border-bottom: 3px solid transparent; }
    nav button.active { border-bottom-color: var(--accent); }
    header input { width: 100%; }
    footer button { min-width: 90px; }
    footer .dim { font-size: 12px; }
  }
</style>

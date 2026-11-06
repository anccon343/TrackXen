<script lang="ts">
  import { live, type CameraStatus } from "../lib/live.svelte";
  import { slots } from "../lib/slots.svelte";
  import { STATE_TEXT, ui } from "../lib/ui.svelte";
  import CompositionControls from "../tracking/CompositionControls.svelte";
  import InitDialog from "../tracking/InitDialog.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Canon's Tracking Execution settings column, for the selected camera. Changes are live at
  // once and kept on disk after Save (like Canon's). Ordered for the operator: what is used during a
  // service first (Auto Tracking, target, on air), the App Settings after it; Save stays in reach.
  // The mouse mode and View Initial Position are above the pictures (VideoToolbar), as in Canon's app.
  let { camera, onsetup }: { camera: CameraStatus; onsetup: (mode: "wizard" | "pages") => void } = $props();
  $effect(() => slots.sync(camera));
  let slot = $derived(slots.camera === camera.id ? slots.slot : undefined);
  let initOpen = $state(false);
  let saved = $state(false);

  async function save() {
    const reply = await slots.command({ type: "save" }, false);
    if (reply.ok) {
      saved = true;
      setTimeout(() => (saved = false), 1500);
    }
  }
</script>

<div class="panel">
  <h2 class="section">App Running</h2>
  <div class="row"><span>Auto Tracking</span>
    <Toggle checked={camera.tracking} onchange={(on) => live.send({ type: "tracking", camera: camera.id, on })} /></div>
  <div class="row"><span>Status</span><span class="badge state-{camera.state}">{STATE_TEXT[camera.state] ?? camera.state}</span></div>
  {#if camera.paused}<p class="warn">Someone moved the camera: tracking waits until it is still.</p>{/if}
  <div class="row"><span>Target</span>
    <span>{camera.target ?? "–"} <button class="small" disabled={camera.target === null}
      onclick={() => live.send({ type: "clear_target", camera: camera.id })}>Clear</button></span></div>
  {#if !slot?.auto_select.enabled && camera.target === null}<p class="hint">Click a person in the picture to make them the target.</p>{/if}

  <h2 class="section">On Air</h2>
  <div class="row"><span>Program</span>
    <span class="badge" class:onair={camera.on_air}>{camera.on_air ? "LIVE" : "off air"}{camera.air > 0 && camera.air < 1 ? " …" : ""}</span></div>
  <div class="segments three" role="group" aria-label="On-air switch">
    <button class:on={camera.on_air_manual === null} onclick={() => live.send({ type: "on_air", camera: camera.id, on: null })}
            title="Follow vMix tally">Auto</button>
    <button class:on={camera.on_air_manual === true} onclick={() => live.send({ type: "on_air", camera: camera.id, on: true })}>On</button>
    <button class:on={camera.on_air_manual === false} onclick={() => live.send({ type: "on_air", camera: camera.id, on: false })}>Off</button>
  </div>
  <p class="hint">While live the camera moves calmly (Setting Wizard → Composition: On-Air Motion). Auto follows vMix.</p>
  <div class="row"><span>Audio follow</span>
    <Toggle checked={camera.audio_follow} onchange={(on) => live.send({ type: "audio_follow", camera: camera.id, on })} /></div>
  {#if camera.cue}<p class="hint">Answering {camera.cue.label}…</p>{/if}

  <h2 class="section">App Settings</h2>
  <div class="segments" role="group" aria-label="App Settings">
    {#each [0, 1, 2, 3, 4] as i}
      <button class:on={camera.slot === i} title={camera.slot_names?.[i]}
              onclick={() => slots.command({ type: "slot", index: i })}>{i + 1}</button>
    {/each}
  </div>
  <div class="slotname dim">{camera.slot_names?.[camera.slot] ?? ""}</div>
  <div class="setup">
    <button onclick={() => onsetup("wizard")}>Setting Wizard</button>
    <button onclick={() => onsetup("pages")}>Settings pages</button>
  </div>

  {#if slots.problems.length}
    <h2 class="section">Check these settings</h2>
    {#each slots.problems as p}<p class="warn">{p}</p>{/each}
  {/if}

  {#if slot}
    {@const af = slot.auto_framing}
    <h2 class="section">Composition</h2>
    <div class="row"><span>Silhouette View</span><Toggle checked={ui.silhouette} onchange={(on) => (ui.silhouette = on)} /></div>
    <CompositionControls {slot} />

    <h2 class="section">Auto Framing</h2>
    <div class="row"><span>Auto Framing</span><Toggle checked={af.enabled} onchange={(on) => slots.patch({ auto_framing: { enabled: on } })} /></div>
    <div class="row sub"><span>Multi-Person</span>
      <Toggle checked={af.multi_person.enabled} disabled={!af.enabled} onchange={(on) => slots.patch({ auto_framing: { multi_person: { enabled: on } } })} /></div>
    <div class="row sub"><span>Face Direction</span>
      <Toggle checked={af.face_direction.enabled} disabled={!af.enabled} onchange={(on) => slots.patch({ auto_framing: { face_direction: { enabled: on } } })} /></div>
    <div class="row sub"><span>Sit/Stand</span>
      <Toggle checked={af.sit_stand.enabled} disabled={!af.enabled || slot.fix_tilt} onchange={(on) => slots.patch({ auto_framing: { sit_stand: { enabled: on } } })} /></div>
    <div class="row"><span>Registered Face Tracking</span>
      <Toggle checked={slot.registered_faces} onchange={(on) => slots.patch({ registered_faces: on })} /></div>
  {/if}

  <p class="hint">Changes work at once; Save keeps them for next time. Change position, size and sensitivity gradually while tracking: big jumps can lose the target.</p>
  <div class="bottom">
    <button onclick={() => (initOpen = true)}>Initialization</button>
    <button class="primary" onclick={save}>{saved ? "Saved" : "Save"}</button>
  </div>
</div>
{#if initOpen}<InitDialog onclose={() => (initOpen = false)} />{/if}

<style>
  .panel { display: flex; flex-direction: column; gap: 4px; }
  .row { display: flex; justify-content: space-between; align-items: center; min-height: 34px; gap: 8px; }
  .segments { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
  .segments.three { grid-template-columns: repeat(3, 1fr); }
  .badge.onair { background: var(--tally-program); }
  .segments button { padding: 0; height: 36px; font-weight: 700; }
  .slotname { font-size: 12px; min-height: 16px; }
  .setup { display: flex; gap: 6px; margin-top: 4px; }
  .setup button { flex: 1; padding: 4px 6px; font-size: 12px; }
  .hint { font-size: 12px; color: var(--dim); margin: 2px 0; }
  .warn { font-size: 12px; color: var(--yellow); margin: 2px 0; }
  .small { min-height: 26px; padding: 2px 8px; font-size: 12px; margin-left: 6px; }
  /* always in reach at the bottom of the panel, however far it is scrolled */
  .bottom { display: flex; gap: 8px; position: sticky; bottom: -10px; margin: 8px -12px -10px; padding: 8px 12px 10px;
            background: var(--panel); border-top: 1px solid var(--line); z-index: 1; }
  .sub { padding-left: 16px; }
  .bottom button { flex: 1; }
  h2.section:first-child { margin-top: 0; }
</style>

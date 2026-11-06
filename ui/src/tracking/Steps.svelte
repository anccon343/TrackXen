<script lang="ts">
  import { live, type CameraStatus } from "../lib/live.svelte";
  import { slots, type AppSettings } from "../lib/slots.svelte";
  import NumberSlider from "../ui/NumberSlider.svelte";
  import Toggle from "../ui/Toggle.svelte";
  import AreaRow from "./AreaRow.svelte";
  import CompositionControls from "./CompositionControls.svelte";
  import FacesStep from "./FacesStep.svelte";
  import OnAirControls from "./OnAirControls.svelte";

  // The ten settings pages of Canon's wizard (docs/reference.md section 3), one at a time, by Canon's step
  // number (the wizard shows them in its own order and groups).
  let { step, slot, camera }: { step: number; slot: AppSettings; camera: CameraStatus } = $props();
  const edges = [["top", "Top edge"], ["bottom", "Bottom edge"], ["left", "Left edge"], ["right", "Right edge"]] as const;
  const range = $derived(slot.shooting_range);
  const presets = Array.from({ length: 16 }, (_, i) => i + 1);
  const deg = (v: number) => `${v.toFixed(1)}°`;
</script>

{#if step === 1}
  <div class="row"><span>Fix Tilt Operation</span><Toggle checked={slot.fix_tilt} onchange={(on) => slots.patch({ fix_tilt: on })} /></div>
  <p class="help">No tilting while tracking. Also turns off the top/bottom range edges and Sit/Stand, and stops zooming in when that would push the target out of the picture.</p>
  <div class="row"><span>Specify Shooting Range</span>
    <Toggle checked={range.enabled} onchange={(on) => slots.patch({ shooting_range: { enabled: on } })} /></div>
  <p class="help">Move the camera to each limit, then Capture: that edge of the current picture becomes the limit. View shows it again.</p>
  {#each edges as [edge, title]}
    <div class="row">
      <span>{title}</span>
      <span class="mono dim">{deg(range[edge])}</span>
      <span class="buttons">
        <button class="small" disabled={!range.enabled || (slot.fix_tilt && (edge === "top" || edge === "bottom"))}
                onclick={() => slots.command({ type: "capture", what: edge })}>Capture</button>
        <button class="small" disabled={!range.enabled} onclick={() => slots.command({ type: "view", what: edge }, false)}>View</button>
      </span>
    </div>
  {/each}
{:else if step === 2}
  <div class="row"><span>Initial Position</span>
    <span class="mono dim">{slot.initial.set ? `${deg(slot.initial.pan)}, ${deg(slot.initial.tilt)}` : "not set"}</span>
    <span class="buttons">
      <button class="small" onclick={() => slots.command({ type: "capture", what: "initial" })}>Capture</button>
      <button class="small" disabled={!slot.initial.set} onclick={() => slots.command({ type: "view", what: "initial" }, false)}>View</button>
    </span></div>
  <p class="help">The view tracking starts from, and where the camera goes when the target is lost. Make it wide enough to find people in. After changing it, re-check the priority display area and the auto select area: they must lie inside it.</p>
  <span class="label">When auto tracking is on and the target is lost</span>
  <label class="radio"><input type="radio" checked={slot.lost_action === "maintain"} onchange={() => slots.patch({ lost_action: "maintain" })} /> Maintain viewing angle</label>
  <label class="radio"><input type="radio" checked={slot.lost_action === "return"} onchange={() => slots.patch({ lost_action: "return" })} /> Return to initial position</label>
  <span class="label">Time before returning to initial position (sec)</span>
  <NumberSlider value={slot.return_after} min={0} max={30} step={1} disabled={slot.lost_action !== "return"}
                onchange={(v) => slots.patch({ return_after: v })} />
{:else if step === 3}
  <p class="help">Choose Set Area in the mouse menu, then drag inside a frame to move it or drag its edges to resize it. The two kinds can't be used together.</p>
  <AreaRow kind="priority" area={slot.priority_area} title="Priority Display Area" />
  <p class="help">Kept in the picture all the time (a screen, a whiteboard); it wins over following the person.</p>
  {#each slot.fixed_areas as area, i}
    <AreaRow kind="fixed" index={i} {area} title="Fixed Viewing Angle Area {i + 1}" capture disabled={slot.priority_area.enabled} />
  {/each}
  <p class="help">While the target is inside the frame the camera holds one shot (a podium). Set the shot with the pan/tilt/zoom sliders and press Capture; placing the frame also takes the current shot.</p>
{:else if step === 4}
  <div class="row"><span>Auto Select</span>
    <Toggle checked={slot.auto_select.enabled} onchange={(on) => slots.patch({ auto_select: { enabled: on } })} /></div>
  <p class="help">On: a person in the auto select area becomes the target. Off: click the target in the picture.</p>
  <span class="label">Tracking Start Time (sec)</span>
  <NumberSlider value={slot.auto_select.start_time} min={0} max={10} step={0.5} digits={1}
                disabled={!slot.auto_select.enabled || slot.registered_faces}
                onchange={(v) => slots.patch({ auto_select: { start_time: v } })} />
  <p class="help">How long someone must be in the area before tracking starts.</p>
  <AreaRow kind="auto_select" area={slot.auto_select.area} title="Auto Select Area" disabled={!slot.auto_select.enabled} />
  <p class="help">Off: the whole picture counts.</p>
  <div class="row"><span>Restart Tracking After Manual Operation</span>
    <Toggle checked={slot.restart_after_manual} onchange={(on) => slots.patch({ restart_after_manual: on })} /></div>
  <p class="help">Moving the camera by hand (vMix, a joystick, BirdUI, this app's PTZ buttons) stops tracking. On: tracking starts again by itself once the camera is still.</p>
{:else if step === 5}
  <CompositionControls {slot} help />
  <OnAirControls {slot} />
{:else if step === 6}
  <p class="help">Never pick a target from here automatically: posters, photos, screens showing people.</p>
  {#each slot.exclusion_areas as area, i}<AreaRow kind="exclusion" index={i} {area} title="Auto Select Exclusion Area {i + 1}" />{/each}
{:else if step === 7}
  <p class="help">A target walking in is let go, and nobody inside is picked: for example the seats of speakers waiting for their turn.</p>
  {#each slot.halting_areas as area, i}<AreaRow kind="halting" index={i} {area} title="Tracking Halting Area {i + 1}" />{/each}
{:else if step === 8}
  {@const af = slot.auto_framing}
  <div class="row"><span>Auto Framing</span><Toggle checked={af.enabled} onchange={(on) => slots.patch({ auto_framing: { enabled: on } })} /></div>
  <p class="help">The shooting range, priority display area and the other areas still come first.</p>

  <div class="row sub"><span>Multi-Person</span>
    <Toggle checked={af.multi_person.enabled} disabled={!af.enabled} onchange={(on) => slots.patch({ auto_framing: { multi_person: { enabled: on } } })} /></div>
  <p class="help sub">People near the target are framed together with them (2-4 people on stage). With only the target in the picture, the normal composition applies. People in halting areas are left out.</p>
  {#if af.enabled && af.multi_person.enabled}
    <span class="label sub">Zoom Speed</span>
    <div class="sub"><NumberSlider value={af.multi_person.zoom_speed} min={1} max={3} onchange={(v) => slots.patch({ auto_framing: { multi_person: { zoom_speed: v } } })} /></div>
    <span class="label sub">Minimum Size When Zooming Out</span>
    <div class="sub"><NumberSlider value={af.multi_person.min_size} min={0} max={3} minLabel="Min" onchange={(v) => slots.patch({ auto_framing: { multi_person: { min_size: v } } })} /></div>
    <p class="help sub">Higher stops zooming out sooner, so people don't get too small.</p>
    <span class="label sub">Composition Transition Speed (Single/Multiple)</span>
    <div class="sub"><NumberSlider value={af.multi_person.transition_speed} min={1} max={3} onchange={(v) => slots.patch({ auto_framing: { multi_person: { transition_speed: v } } })} /></div>
    <p class="help sub">Lower changes more slowly between one person and the group.</p>
  {/if}

  <div class="row sub"><span>Face Direction</span>
    <Toggle checked={af.face_direction.enabled} disabled={!af.enabled} onchange={(on) => slots.patch({ auto_framing: { face_direction: { enabled: on } } })} /></div>
  <p class="help sub">Leaves space on the side the target's face points to. Needs the face to be seen; not inside fixed or halting areas.</p>
  {#if af.enabled && af.face_direction.enabled}
    <span class="label sub">Adjustment Sensitivity</span>
    <div class="sub"><NumberSlider value={af.face_direction.sensitivity} min={1} max={3} onchange={(v) => slots.patch({ auto_framing: { face_direction: { sensitivity: v } } })} /></div>
    <span class="label sub">Space Size</span>
    <div class="sub"><NumberSlider value={af.face_direction.space} min={1} max={3} onchange={(v) => slots.patch({ auto_framing: { face_direction: { space: v } } })} /></div>
    <span class="label sub">When the target faces forward</span>
    <label class="radio sub"><input type="radio" checked={af.face_direction.forward === "maintain"} onchange={() => slots.patch({ auto_framing: { face_direction: { forward: "maintain" } } })} /> Maintain</label>
    <label class="radio sub"><input type="radio" checked={af.face_direction.forward === "center"} onchange={() => slots.patch({ auto_framing: { face_direction: { forward: "center" } } })} /> Center</label>
  {/if}

  <div class="row sub"><span>Sit/Stand</span>
    <Toggle checked={af.sit_stand.enabled} disabled={!af.enabled || slot.fix_tilt} onchange={(on) => slots.patch({ auto_framing: { sit_stand: { enabled: on } } })} /></div>
  <p class="help sub">A larger display size while the target sits, a smaller one standing. Turning it on also turns on Display Size (the standing size) and Auto Zoom. Not with Fix Tilt Operation.</p>
  {#if af.enabled && af.sit_stand.enabled}
    <span class="label sub">Display Size After Sitting Down</span>
    <div class="sub"><NumberSlider value={af.sit_stand.sit_size} min={0.2} max={0.95} step={0.05} digits={2} minLabel="small" maxLabel="large" onchange={(v) => slots.patch({ auto_framing: { sit_stand: { sit_size: v } } })} /></div>
    <span class="label sub">Display Size After Standing Up</span>
    <div class="sub"><NumberSlider value={af.sit_stand.stand_size} min={0.2} max={0.95} step={0.05} digits={2} minLabel="small" maxLabel="large" onchange={(v) => slots.patch({ auto_framing: { sit_stand: { stand_size: v } } })} /></div>
  {/if}
{:else if step === 9}
  <FacesStep {slot} {camera} />
{:else if step === 10}
  <p class="help">What tracking does after a camera preset is recalled (shared by all five App Settings). On: tracking starts with the chosen App Settings once the camera is there. Off: tracking stops. Blank: like any manual operation (Tracking Target Auto Select page).</p>
  <table>
    <thead><tr><th>Preset</th><th>Auto Tracking</th><th>App Settings</th><th></th></tr></thead>
    <tbody>
      {#each presets as n}
        {@const link = slots.data?.preset_links[String(n)] ?? { auto_tracking: "", slot: 0 }}
        <tr>
          <td>{n}</td>
          <td><select value={link.auto_tracking} onchange={(e) => slots.command({ type: "preset_link", number: n, auto_tracking: (e.currentTarget as HTMLSelectElement).value, slot: link.slot })}>
            <option value="">–</option><option value="on">On</option><option value="off">Off</option></select></td>
          <td><select value={link.slot} disabled={link.auto_tracking !== "on"} onchange={(e) => slots.command({ type: "preset_link", number: n, auto_tracking: link.auto_tracking, slot: +(e.currentTarget as HTMLSelectElement).value })}>
            {#each [0, 1, 2, 3, 4] as i}<option value={i}>{i + 1}</option>{/each}</select></td>
          <td><button class="small" onclick={() => live.send({ type: "preset", camera: camera.id, number: n })}>View</button></td>
        </tr>
      {/each}
    </tbody>
  </table>
  <button onclick={() => slots.command({ type: "preset_links_reset" })}>Initialization</button>
{/if}

<style>
  .row { display: flex; align-items: center; gap: 8px; min-height: 38px; }
  .row > span:first-child { flex: 1; }
  .buttons { display: flex; gap: 4px; }
  .label { font-size: 12px; color: var(--dim); margin-top: 8px; display: block; }
  .help { font-size: 12px; color: var(--dim); margin: 0 0 6px; }
  .radio { display: flex; gap: 8px; align-items: center; min-height: 32px; }
  .sub { margin-left: 16px; }
  .radio input { min-height: 0; accent-color: var(--accent); }
  .small { min-height: 28px; padding: 2px 10px; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
  th { text-align: left; font-size: 12px; color: var(--dim); font-weight: 600; padding: 4px; }
  td { padding: 3px 4px; border-top: 1px solid var(--line); }
  td select { min-height: 30px; padding: 2px 6px; }
</style>

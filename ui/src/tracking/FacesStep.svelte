<script lang="ts">
  import { onDestroy } from "svelte";
  import type { CameraStatus } from "../lib/live.svelte";
  import { faces } from "../lib/faces.svelte";
  import { slots, type AppSettings } from "../lib/slots.svelte";
  import { ui } from "../lib/ui.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Canon's Registered Face Tracking page: 7 face slots per camera (order = priority), each on or
  // off, Register (a frame on the picture turns green when the face is usable) and Delete.
  let { slot, camera }: { slot: AppSettings; camera: CameraStatus } = $props();
  let refs = $derived(faces.cameras[camera.id] ?? []);
  let others = $derived(faces.list.filter((f) => !refs.some((r) => r.id === f.id)));
  let registering = $derived(faces.guide === camera.id);
  let name = $state("");
  let capturing = $state(false);
  let selected = $state<string | null>(null);

  let rev = $derived(camera.settings_rev); // the engine bumps it when this camera's face list changes
  let pending = $derived(camera.face_capturing);
  $effect(() => {
    void rev;
    faces.load();
  });
  $effect(() => {
    if (capturing && !pending) {
      capturing = false;
      faces.stopGuide();
      ui.notify(`${name || "Face"} registered`);
      name = "";
    }
  });
  onDestroy(() => {
    if (faces.guide === camera.id) faces.stopGuide();
  });

  function move(id: string, by: number) {
    const ids = refs.map((r) => r.id);
    const i = ids.indexOf(id);
    if (i + by < 0 || i + by >= ids.length) return;
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    faces.command(camera.id, { type: "face_order", ids });
  }

  async function capture() {
    const reply = await faces.command(camera.id, { type: "face_capture", name });
    capturing = reply.ok;
  }
</script>

<div class="row"><span>Registered Face Tracking</span>
  <Toggle checked={slot.registered_faces} onchange={(on) => slots.patch({ registered_faces: on })} /></div>
<p class="help">On: only the registered faces switched on below are picked as the target, also after the target is lost.
  If several appear at once, the highest one in the list wins; once someone is tracked, a higher one appearing doesn't take over.</p>

<ol class="faces">
  {#each refs as ref, i (ref.id)}
    <li class:selected={selected === ref.id}>
      <span class="order">
        <button class="tiny" aria-label="higher priority" disabled={i === 0} onclick={() => move(ref.id, -1)}>▲</button>
        <button class="tiny" aria-label="lower priority" disabled={i === refs.length - 1} onclick={() => move(ref.id, 1)}>▼</button>
      </span>
      <span class="num">{i + 1}</span>
      <img src="/api/faces/{ref.id}.jpg?v={faces.version}" alt="" width="40" height="40" />
      <input type="text" maxlength="40" value={faces.name(ref.id)} aria-label="name"
             onchange={(e) => faces.command(camera.id, { type: "face_update", id: ref.id, name: (e.currentTarget as HTMLInputElement).value })}
             onfocus={() => (selected = ref.id)} />
      <Toggle checked={ref.enabled} onchange={(on) => faces.command(camera.id, { type: "face_update", id: ref.id, enabled: on })} />
    </li>
  {/each}
  {#each Array(Math.max(0, 7 - refs.length)) as _, i}
    <li class="empty"><span class="order"></span><span class="num">{refs.length + i + 1}</span><span class="dim">empty</span></li>
  {/each}
</ol>

{#if registering}
  <div class="register">
    <p><b>Registering on {camera.name}.</b> Ask the person to face the camera, centre the face in the frame
      and fill it (zoom in with the slider). The frame turns green when the face is usable.</p>
    <p class="status" class:ok={camera.face_guide?.ok}>{camera.face_guide ? (camera.face_guide.ok ? "Face is usable" : camera.face_guide.problem) : "Looking…"}</p>
    <input type="text" maxlength="40" placeholder="Name (for example Pastor)" bind:value={name} />
    <div class="buttons">
      <button onclick={() => faces.stopGuide()}>Cancel</button>
      <button class="primary" disabled={!camera.face_guide?.ok || capturing} onclick={capture}>{capturing ? "Capturing…" : "Capture"}</button>
    </div>
  </div>
{:else}
  <div class="buttons">
    <button disabled={!selected} onclick={() => { if (selected) faces.command(camera.id, { type: "face_delete", id: selected }); selected = null; }}>Delete</button>
    <button class="accent" disabled={refs.length >= 7} onclick={() => faces.startGuide(camera.id)}>Register</button>
  </div>
  {#if others.length && refs.length < 7}
    <span class="label">Add a face registered on another camera</span>
    <div class="others">
      {#each others as f (f.id)}
        <button class="other" onclick={() => faces.command(camera.id, { type: "face_add", id: f.id })} title="Add {f.name}">
          <img src="/api/faces/{f.id}.jpg?v={faces.version}" alt="" width="32" height="32" /> {f.name}</button>
      {/each}
    </div>
  {/if}
{/if}

<p class="help">Register in the real conditions (same glasses, same camera angle). Faces fail when turned away,
  too small, too dark or bright, partly covered, or with a very different expression.</p>
<p class="help"><b>Privacy:</b> ask people before registering their face. Face data stays on this PC only; delete it when
  it is no longer needed (Settings → System → Delete all face data).</p>

<style>
  .row { display: flex; align-items: center; gap: 8px; min-height: 38px; }
  .row > span:first-child { flex: 1; }
  .help { font-size: 12px; color: var(--dim); margin: 0 0 6px; }
  .label { font-size: 12px; color: var(--dim); margin-top: 8px; display: block; }
  .faces { list-style: none; padding: 0; margin: 6px 0; display: flex; flex-direction: column; gap: 4px; }
  .faces li { display: flex; align-items: center; gap: 6px; min-height: 46px; padding: 2px 4px; border-radius: var(--radius);
              background: var(--panel-2); border: 1px solid transparent; }
  .faces li.selected { border-color: var(--accent); }
  .faces li.empty { opacity: 0.5; }
  .order { display: flex; flex-direction: column; width: 26px; }
  .tiny { min-height: 18px; padding: 0; font-size: 9px; line-height: 1; }
  .num { width: 18px; text-align: center; font-weight: 700; }
  img { border-radius: 3px; object-fit: cover; background: #111; }
  .faces input { flex: 1; min-width: 0; }
  .buttons { display: flex; gap: 8px; justify-content: flex-end; margin: 6px 0; }
  .buttons button { min-width: 96px; }
  .register { border: 1px solid var(--line); border-radius: var(--radius); padding: 8px 10px; margin: 6px 0;
              display: flex; flex-direction: column; gap: 6px; }
  .register p { margin: 0; font-size: 13px; }
  .status { color: var(--yellow); font-weight: 600; }
  .status.ok { color: #3ddc6c; }
  .others { display: flex; flex-wrap: wrap; gap: 6px; }
  .other { display: flex; align-items: center; gap: 6px; padding: 2px 8px 2px 2px; }
</style>

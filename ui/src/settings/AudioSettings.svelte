<script lang="ts">
  import { faces } from "../lib/faces.svelte";
  import { getJSON, live } from "../lib/live.svelte";
  import { ui } from "../lib/ui.svelte";
  import NumberSlider from "../ui/NumberSlider.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Audio-follow: a mic that starts speaking sends a camera to that person. Each mic can be tied to
  // a registered face and to a preset per camera; the list order is the priority.
  interface Mapping { channel: number; label: string; face: string; presets: Record<string, number>; cameras: string[] }
  interface Audio {
    enabled: boolean; threshold_db: number; attack: number; hold: number;
    live_camera: "wait" | "slow"; manual_grace: number; mappings: Mapping[];
  }
  let audio = $state<Audio | null>(null);
  let mixer = $derived(live.status?.mixer ?? null);
  let cameras = $derived(live.status?.cameras ?? []);

  const load = () => getJSON<{ audio: Audio }>("/api/config").then((c) => (audio = c.audio));
  $effect(() => {
    load();
    faces.ensure();
  });

  async function save(changes: Partial<Audio>) {
    if (!audio) return;
    Object.assign(audio, changes);
    const reply = await live.request({ type: "audio_settings", settings: $state.snapshot(audio) });
    if (reply.ok) {
      audio = reply.audio as Audio;
      return;
    }
    ui.notify(reply.error ?? "Not possible");
    await load().catch(() => undefined); // show what the engine has (the refused value would make every later save fail too)
  }

  const mappingOf = (ch: number) => audio?.mappings.find((m) => m.channel === ch);
  function use(ch: number, on: boolean) {
    if (!audio) return;
    const name = mixer?.channels[ch - 1]?.name ?? "";
    const mappings = on ? [...audio.mappings, { channel: ch, label: name, face: "", presets: {}, cameras: [] }]
                        : audio.mappings.filter((m) => m.channel !== ch);
    save({ mappings });
  }
  function edit(ch: number, change: Partial<Mapping>) {
    if (!audio) return;
    save({ mappings: audio.mappings.map((m) => (m.channel === ch ? { ...m, ...change } : m)) });
  }
  function preset(ch: number, camera: string, value: string) {
    const m = mappingOf(ch);
    if (!m) return;
    const presets = { ...m.presets };
    if (value === "") delete presets[camera];
    else presets[camera] = Math.max(1, Math.min(128, Math.round(+value)));
    edit(ch, { presets });
  }
  function move(ch: number, by: number) {
    if (!audio) return;
    const list = [...audio.mappings];
    const i = list.findIndex((m) => m.channel === ch);
    if (i + by < 0 || i + by >= list.length) return;
    [list[i], list[i + by]] = [list[i + by], list[i]];
    save({ mappings: list });
  }
  const meter = (db: number) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
</script>

{#if audio}
  <div class="row"><span>Audio follow</span><Toggle checked={audio.enabled} onchange={(on) => save({ enabled: on })} /></div>
  <p class="dim">Needs the mixer (Settings → vMix & mixer). Each camera can be left out with its own Audio follow switch
    on the tracking panel. A camera the operator just used is left alone.</p>

  <h2 class="section">Speech detection</h2>
  <span class="label">Threshold (dB): a mic louder than this is speaking</span>
  <NumberSlider value={audio.threshold_db} min={-80} max={-5} step={1} minLabel="-80" maxLabel="-5" onchange={(v) => save({ threshold_db: v })} />
  <span class="label">Attack (s): how long it must stay loud</span>
  <NumberSlider value={audio.attack} min={0} max={3} step={0.1} digits={1} onchange={(v) => save({ attack: v })} />
  <span class="label">Hold (s): pauses shorter than this don't end the speech</span>
  <NumberSlider value={audio.hold} min={0} max={15} step={0.5} digits={1} onchange={(v) => save({ hold: v })} />
  <span class="label">Leave cameras alone after the operator used them (s)</span>
  <NumberSlider value={audio.manual_grace} min={0} max={120} step={5} onchange={(v) => save({ manual_grace: v })} />
  <span class="label">When only the live camera could answer</span>
  <label class="radio"><input type="radio" checked={audio.live_camera === "wait"} onchange={() => save({ live_camera: "wait" })} /> Wait until it is off Program</label>
  <label class="radio"><input type="radio" checked={audio.live_camera === "slow"} onchange={() => save({ live_camera: "slow" })} /> Take the person over slowly (no preset jump)</label>

  <h2 class="section">Microphones</h2>
  <p class="dim">Switch on the mics to follow. For each: who uses it (a registered face, optional) and, per camera,
    the preset showing their place. Cameras with neither don't answer that mic. Higher in the list wins when two speak at once.</p>
  <div class="mics">
    {#each Array.from({ length: 16 }, (_, i) => i + 1) as ch}
      {@const info = mixer?.channels[ch - 1]}
      {@const m = mappingOf(ch)}
      <div class="mic" class:used={!!m} class:speaking={info?.speaking}>
        <div class="head">
          <span class="ch">{ch}</span>
          <span class="name">{info?.name || `Ch ${ch}`}{info?.muted ? " (muted)" : ""}</span>
          <span class="meter"><span style="width: {meter(info?.level ?? -90)}%"></span>
            <i style="left: {meter(audio.threshold_db)}%"></i></span>
          <Toggle checked={!!m} label="" onchange={(on) => use(ch, on)} />
        </div>
        {#if m}
          <div class="body">
            <input type="text" maxlength="40" value={m.label} placeholder="Label (for example Pastor)"
                   onchange={(e) => edit(ch, { label: (e.currentTarget as HTMLInputElement).value })} />
            <select value={m.face} onchange={(e) => edit(ch, { face: (e.currentTarget as HTMLSelectElement).value })}>
              <option value="">– nobody registered –</option>
              {#each faces.list as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
            </select>
            <div class="presets">
              {#each cameras as cam (cam.id)}
                <label>{cam.name} preset
                  <input type="number" min="1" max="128" value={m.presets[cam.id] ?? ""} placeholder="–"
                         onchange={(e) => preset(ch, cam.id, (e.currentTarget as HTMLInputElement).value)} /></label>
              {/each}
            </div>
            <div class="order">
              <button class="small" onclick={() => move(ch, -1)} aria-label="higher priority">▲</button>
              <button class="small" onclick={() => move(ch, 1)} aria-label="lower priority">▼</button>
              <span class="dim">priority {audio.mappings.findIndex((x) => x.channel === ch) + 1}</span>
            </div>
          </div>
        {/if}
      </div>
    {/each}
  </div>
{:else}
  <p class="dim">Loading…</p>
{/if}

<style>
  .row { display: flex; justify-content: space-between; align-items: center; min-height: 36px; }
  .label { font-size: 12px; color: var(--dim); margin-top: 8px; display: block; }
  .radio { display: flex; gap: 8px; align-items: center; min-height: 30px; }
  .radio input { min-height: 0; accent-color: var(--accent); }
  .mics { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 8px; }
  .mic { background: var(--panel-2); border: 1px solid transparent; border-radius: var(--radius); padding: 6px 8px; }
  .mic.used { border-color: var(--line); }
  .mic.speaking { border-color: #3ddc6c; }
  .head { display: flex; align-items: center; gap: 8px; }
  .ch { width: 22px; font-weight: 700; text-align: center; }
  .name { width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
  .meter { flex: 1; height: 8px; background: #111; border-radius: 4px; position: relative; overflow: hidden; }
  .meter span { position: absolute; left: 0; top: 0; bottom: 0; background: #3ddc6c; transition: width 0.1s; }
  .meter i { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--accent); }
  .body { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .presets { display: flex; flex-wrap: wrap; gap: 6px; }
  .presets label { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--dim); }
  .presets input { width: 64px; }
  .order { display: flex; align-items: center; gap: 4px; }
  .small { min-height: 26px; padding: 0 8px; font-size: 11px; }
</style>

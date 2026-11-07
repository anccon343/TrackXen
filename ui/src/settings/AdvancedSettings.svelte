<script lang="ts">
  import { live } from "../lib/live.svelte";
  import { settings } from "../lib/config.svelte";
  import NumberSlider from "../ui/NumberSlider.svelte";
  import Field from "../ui/Field.svelte";
  import Toggle from "../ui/Toggle.svelte";

  // Advanced mode: how certain the system must be before (and while) it tracks someone.
  // Changes apply immediately to the running tracker and are saved.
  let unlocked = $state(localStorage.getItem("trackxen.advanced") === "1");
  let d = $derived(settings.draft?.detection);

  function setUnlocked(v: boolean) {
    unlocked = v;
    try { localStorage.setItem("trackxen.advanced", v ? "1" : "0"); } catch { /* private window */ }
  }
  function change(key: string, value: number) {
    settings.draft!.detection[key] = value;
    settings.saved!.detection[key] = value; // applied live, so it's no longer an unsaved change
    live.send({ type: "detection", settings: { [key]: value } });
  }
  const defaults: Record<string, number> = {
    start_confidence: 0.35, confirm_frames: 2, match_confidence: 0.3, keep_confidence: 0.1,
    keep_missing_seconds: 1.0, match_overlap: 0.2,
  };
</script>

<Field label="Advanced mode" help="Unlocks the settings below. The defaults suit most churches and stages.">
  <Toggle checked={unlocked} onchange={setUnlocked} />
</Field>

{#if d}
  <fieldset disabled={!unlocked}>
    <h2 class="section">Recognition certainty</h2>
    <Field label="Start tracking a person at" help="How sure the detector must be before a new person gets an ID. Higher: fewer false people (posters, screens), but people in poor light are picked up later.">
      <NumberSlider value={d.start_confidence} min={0.05} max={0.95} step={0.05} digits={2} disabled={!unlocked}
                    onchange={(v) => change("start_confidence", v)} />
    </Field>
    <Field label="...seen this many frames in a row" help="More frames: steadier, but a new person is tracked a little later.">
      <NumberSlider value={d.confirm_frames} min={1} max={30} disabled={!unlocked} onchange={(v) => change("confirm_frames", v)} />
    </Field>
    <Field label="Sure detection" help="Detections at least this sure are matched to people first.">
      <NumberSlider value={d.match_confidence} min={0.05} max={0.95} step={0.05} digits={2} disabled={!unlocked}
                    onchange={(v) => change("match_confidence", v)} />
    </Field>
    <Field label="Keep tracking down to" help="Weaker detections still keep a known person (behind the podium, backlit). Lower: holds on longer, but may follow a wrong shape.">
      <NumberSlider value={d.keep_confidence} min={0.01} max={0.9} step={0.01} digits={2} disabled={!unlocked}
                    onchange={(v) => change("keep_confidence", v)} />
    </Field>
    <Field label="Keep an unseen person for (s)" help="How long someone stays known without any detection.">
      <NumberSlider value={d.keep_missing_seconds} min={0.1} max={10} step={0.1} digits={1} disabled={!unlocked}
                    onchange={(v) => change("keep_missing_seconds", v)} />
    </Field>
    <Field label="Box overlap needed to match" help="How much a detection must overlap where the person was. Lower: follows fast movement, but people walking past each other mix up more easily.">
      <NumberSlider value={d.match_overlap} min={0.05} max={0.9} step={0.05} digits={2} disabled={!unlocked}
                    onchange={(v) => change("match_overlap", v)} />
    </Field>
    <div class="actions">
      <button disabled={!unlocked} onclick={() => Object.entries(defaults).forEach(([k, v]) => change(k, v))}>Restore defaults</button>
    </div>
  </fieldset>
{/if}

<style>
  fieldset { border: none; padding: 0; margin: 0; }
  fieldset:disabled { opacity: 0.5; }
  .actions { display: flex; justify-content: flex-end; margin-top: 8px; }
</style>

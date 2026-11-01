<script lang="ts">
  // On/off switch in the Canon style: orange when on, with an "On" label.
  let { checked = false, label = "On", disabled = false, onchange }: {
    checked?: boolean; label?: string; disabled?: boolean; onchange?: (value: boolean) => void;
  } = $props();
</script>

<button type="button" class="toggle" class:on={checked} {disabled} role="switch" aria-checked={checked}
        onclick={() => onchange?.(!checked)}>
  <span class="track"><span class="knob"></span></span>
  <span class="label">{label}</span>
</button>

<style>
  .toggle { background: none; border: none; padding: 0; display: inline-flex; align-items: center; gap: 6px;
            min-height: 30px; }
  .toggle:hover { background: none; }
  .track { width: 40px; height: 22px; border-radius: 11px; background: #111; border: 1px solid var(--line);
           position: relative; transition: background 0.15s; }
  .knob { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
          background: #ddd; transition: left 0.15s; }
  .on .track { background: var(--accent); border-color: var(--accent); }
  .on .knob { left: 20px; }
  .label { font-size: 12px; color: var(--dim); }
</style>

<script lang="ts">
  // Slider with min/max labels, − / + buttons and the value.
  let { value, min, max, step = 1, digits = 0, minLabel, maxLabel, disabled = false, onchange }: {
    value: number; min: number; max: number; step?: number; digits?: number;
    minLabel?: string; maxLabel?: string; disabled?: boolean; onchange: (value: number) => void;
  } = $props();

  const clamp = (v: number) => Math.min(max, Math.max(min, Math.round(v / step) * step));
  const set = (v: number) => onchange(Number(clamp(v).toFixed(6)));
</script>

<div class="slider">
  <div class="track-col">
    <input type="range" {min} {max} {step} {value} {disabled}
           oninput={(e) => set(+(e.currentTarget as HTMLInputElement).value)} />
    <div class="ends"><span>{minLabel ?? min}</span><span>{maxLabel ?? max}</span></div>
  </div>
  <div class="stepper">
    <button type="button" {disabled} onclick={() => set(value - step)} aria-label="decrease">−</button>
    <button type="button" {disabled} onclick={() => set(value + step)} aria-label="increase">+</button>
  </div>
  <span class="value mono">{value.toFixed(digits)}</span>
</div>

<style>
  .slider { display: flex; align-items: center; gap: 8px; width: 100%; }
  .track-col { flex: 1; min-width: 80px; }
  input[type="range"] { width: 100%; accent-color: var(--accent); min-height: 24px; padding: 0; background: none;
                        border: none; }
  .ends { display: flex; justify-content: space-between; font-size: 10px; color: var(--dim); margin-top: -2px; }
  .stepper { display: flex; }
  .stepper button { width: 32px; min-height: 30px; padding: 0; border-radius: 0; }
  .stepper button:first-child { border-radius: var(--radius) 0 0 var(--radius); }
  .stepper button:last-child { border-radius: 0 var(--radius) var(--radius) 0; border-left: none; }
  .value { width: 40px; text-align: right; }
</style>

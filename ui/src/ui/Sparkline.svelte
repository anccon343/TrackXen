<script lang="ts">
  // Small live graph for the statistics page (last few minutes of once-a-second samples).
  let { values, max = 100, color = "#d9730d", label = "", unit = "" }: {
    values: number[]; max?: number; color?: string; label?: string; unit?: string;
  } = $props();

  const W = 240, H = 48;
  let top = $derived(Math.max(max, ...values, 1));
  let points = $derived(values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * W},${H - (v / top) * (H - 4) - 2}`).join(" "));
  let last = $derived(values.length ? values[values.length - 1] : undefined);
</script>

<figure>
  <figcaption><span>{label}</span><span class="mono">{last === undefined ? "–" : `${last.toFixed(1)}${unit}`}</span></figcaption>
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="{label} graph">
    <polyline {points} fill="none" stroke={color} stroke-width="1.5" vector-effect="non-scaling-stroke" />
  </svg>
</figure>

<style>
  figure { margin: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius);
           padding: 6px 8px; }
  figcaption { display: flex; justify-content: space-between; font-size: 12px; color: var(--dim); }
  svg { width: 100%; height: 48px; display: block; }
</style>

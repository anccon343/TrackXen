<script lang="ts">
  import { fmt, live, type Sample } from "../lib/live.svelte";

  // Bottom bar like vMix: Statistics button, measured CPU/GPU load, detection time, problem lights.
  let { onstatistics }: { onstatistics: () => void } = $props();
  let s = $derived(live.status?.stats as Sample | undefined);
  let gpu = $derived(s?.gpus?.[0]);
  let problems = $derived(live.status?.problems ?? []);
  let detect = $derived(s?.app ? Math.max(0, ...Object.entries(s.app).filter(([k]) => k.endsWith(".detect_ms_p50")).map(([, v]) => v)) : undefined);
  let worst = $derived(problems.some((p) => p.level === "error") ? "error" : problems.length ? "warning" : "ok");
</script>

<footer class="bar mono">
  <button onclick={onstatistics}>Statistics</button>
  <span title="Detection time (median, slowest camera)">Detect {fmt(detect)} ms</span>
  <span title="GPU busy time (NVIDIA) and this app's share">GPU {gpu ? `${gpu.load_percent}% (app ${gpu.app_load_percent ?? 0}%)` : "–"}</span>
  <span title="CPU: whole PC and this app">CPU {fmt(s?.cpu_percent, 0)}% (app {fmt(s?.app_cpu_percent)}%)</span>
  <span class="grow"></span>
  <span class="problems" title={problems.map((p) => `${p.source}: ${p.message}`).join("\n") || "No problems"}>
    <span class="light {worst}"></span>
    {problems.length ? `${problems.length} problem${problems.length > 1 ? "s" : ""}: ${problems[0].source} ${problems[0].message}` : "All OK"}
  </span>
  <span class="dim">{live.status?.engine ?? ""}</span>
</footer>

<style>
  .bar { display: flex; align-items: center; gap: 16px; padding: 4px 10px; background: var(--rail);
         border-top: 1px solid var(--line); font-size: 12px; white-space: nowrap; overflow: hidden; }
  .bar button { min-height: 28px; padding: 2px 12px; font-size: 12px; }
  .grow { flex: 1; }
  .problems { display: inline-flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; }
</style>

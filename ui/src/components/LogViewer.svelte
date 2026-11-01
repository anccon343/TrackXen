<script lang="ts">
  import { getJSON } from "../lib/live.svelte";

  // Statistics history: pick a day from the log files, see its graphs and its events (what
  // happened when), and download the files. Everything comes from the logs TrackXen writes itself.
  interface Day { day: string; bytes: number }
  interface Point { time: string; cpu: number; app_cpu: number; gpu: number; gpu_temp: number; gpu_mem: number;
                    cams: Record<string, { source_fps?: number; detections_per_s?: number; detect_ms_p50?: number; pipeline_ms_p50?: number }> }
  interface Event { time: string; level: string; kind: string; message: string; source?: string }

  let days = $state<Day[]>([]);
  let day = $state("");
  let points = $state<Point[]>([]);
  let events = $state<Event[]>([]);
  let level = $state<"all" | "warning" | "error">("all");
  let search = $state("");
  let loading = $state(false);

  $effect(() => {
    getJSON<Day[]>("/api/logs").then((d) => {
      days = d;
      if (!day && d.length) day = d[0].day;
    });
  });
  $effect(() => {
    if (!day) return;
    loading = true;
    Promise.all([getJSON<Point[]>(`/api/logs/${day}/stats?points=1500`), getJSON<Event[]>(`/api/logs/${day}/events`)])
      .then(([p, e]) => { points = p; events = e; })
      .finally(() => (loading = false));
  });

  let cams = $derived([...new Set(points.flatMap((p) => Object.keys(p.cams)))].sort());
  let shown = $derived(events.filter((e) => (level === "all" || e.level === level || (level === "warning" && e.level === "error"))
    && (!search || `${e.kind} ${e.message} ${e.source ?? ""}`.toLowerCase().includes(search.toLowerCase()))).slice(-1000).reverse());
  const label = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;

  // one SVG line chart per measure, over the day's time
  const W = 600, H = 70;
  function line(values: (number | undefined)[], top: number): string {
    return values.map((v, i) => v === undefined ? "" : `${(i / Math.max(1, values.length - 1)) * W},${H - (Math.min(v, top) / top) * (H - 4) - 2}`)
      .filter(Boolean).join(" ");
  }
  const charts = $derived([
    { label: "CPU, whole PC (%)", top: 100, series: [{ name: "PC", color: "#d9730d", values: points.map((p) => p.cpu) },
                                                     { name: "TrackXen", color: "#3a8ee6", values: points.map((p) => p.app_cpu) }] },
    { label: "GPU busy (%)", top: 100, series: [{ name: "GPU", color: "#2ecc71", values: points.map((p) => p.gpu) }] },
    { label: "Detections per second", top: 35, series: cams.map((c, i) => ({ name: c, color: ["#d9730d", "#3a8ee6", "#2ecc71", "#e040fb"][i % 4],
                                                                            values: points.map((p) => p.cams[c]?.detections_per_s) })) },
    { label: "Frame → result (ms, median)", top: 80, series: cams.map((c, i) => ({ name: c, color: ["#d9730d", "#3a8ee6", "#2ecc71", "#e040fb"][i % 4],
                                                                                  values: points.map((p) => p.cams[c]?.pipeline_ms_p50) })) },
  ]);
</script>

<div class="viewer">
  <div class="bar">
    <label>Day <select bind:value={day}>{#each days as d (d.day)}<option value={d.day}>{label(d.day)} ({(d.bytes / 1e6).toFixed(1)} MB)</option>{/each}</select></label>
    {#if day}
      <a href="/api/logs/{day}/file/text" download>Text log</a>
      <a href="/api/logs/{day}/file/events" download>Events (JSON lines)</a>
      <a href="/api/logs/{day}/file/stats" download>Samples (JSON lines)</a>
    {/if}
    {#if loading}<span class="dim">Loading…</span>{/if}
  </div>
  {#if points.length}
    <div class="span dim mono">{points[0].time.slice(11)} – {points[points.length - 1].time.slice(11)}, {points.length} points</div>
    <div class="charts">
      {#each charts as c (c.label)}
        <figure>
          <figcaption><span>{c.label}</span><span class="legend">{#each c.series as s (s.name)}<i style="background: {s.color}"></i>{s.name} {/each}</span></figcaption>
          <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label={c.label}>
            {#each c.series as s (s.name)}<polyline points={line(s.values, c.top)} fill="none" stroke={s.color} stroke-width="1.2" vector-effect="non-scaling-stroke" />{/each}
          </svg>
        </figure>
      {/each}
    </div>
  {:else if day && !loading}
    <p class="dim">No samples for this day.</p>
  {/if}
  <h2 class="section">Events</h2>
  <div class="bar">
    <select bind:value={level}><option value="all">All</option><option value="warning">Warnings and errors</option><option value="error">Errors</option></select>
    <input type="text" placeholder="Search (camera, target, vMix…)" bind:value={search} />
    <span class="dim">{shown.length} shown{shown.length === 1000 ? " (newest 1000)" : ""}</span>
  </div>
  <div class="events mono">
    {#each shown as e, i (i)}
      <div class="event {e.level}"><span class="t">{e.time.slice(11, 19)}</span><span class="src">{e.source ?? ""}</span>
        <span class="kind">{e.kind}</span><span>{e.message}</span></div>
    {/each}
  </div>
</div>

<style>
  .viewer { display: flex; flex-direction: column; gap: 8px; }
  .bar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
  .bar a { color: var(--accent); font-size: 13px; }
  .bar input { flex: 1; min-width: 160px; }
  .span { font-size: 12px; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 8px; }
  figure { margin: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 8px; }
  figcaption { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--dim); }
  .legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin: 0 3px 0 6px; }
  svg { width: 100%; height: 70px; display: block; }
  .events { max-height: 420px; overflow-y: auto; font-size: 12px; background: #111; border-radius: var(--radius); padding: 4px 8px; }
  .event { display: grid; grid-template-columns: 64px 48px 80px 1fr; gap: 8px; padding: 2px 0; border-bottom: 1px solid #1c1c1c; }
  .event.warning { color: var(--yellow); }
  .event.error { color: #ff6b5b; }
  .t, .src, .kind { color: var(--dim); }
  .event.warning .t, .event.error .t { color: inherit; }
</style>

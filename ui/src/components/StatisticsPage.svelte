<script lang="ts">
  import { fmt, live, postJSON, stat, type Sample } from "../lib/live.svelte";
  import Sparkline from "../ui/Sparkline.svelte";
  import LogViewer from "./LogViewer.svelte";

  // Statistics like vMix's window: every number is measured (see docs/research.md section 3.5).
  let sample = $derived(live.status?.stats as Sample | undefined);
  let cameras = $derived(live.status?.cameras ?? []);
  let gpu = $derived(sample?.gpus?.[0]);
  let engines = $derived(sample?.gpu_engines?.find((e) => e.app_memory_mb) ?? sample?.gpu_engines?.[0]);
  let logText = $state("");
  let tab = $state<"live" | "history">("live");

  async function loadLog() {
    try {
      logText = await (await fetch("/api/log?lines=200")).text();
    } catch {
      logText = "(log not available)";
    }
  }
  $effect(() => {
    loadLog();
    const t = setInterval(loadLog, 3000);
    return () => clearInterval(t);
  });
</script>

<div class="stats">
  <div class="head">
    <h1>Statistics</h1>
    <nav>
      <button class:on={tab === "live"} onclick={() => (tab = "live")}>Live</button>
      <button class:on={tab === "history"} onclick={() => (tab = "history")}>History</button>
    </nav>
    {#if tab === "live"}<button onclick={() => postJSON("/api/statistics/reset", {})}>Reset</button>{/if}
  </div>

  {#if tab === "history"}
    <LogViewer />
  {:else}
  <div class="scroll">
    <table class="mono">
      <thead>
        <tr>
          <th>Camera</th><th>State</th><th title="Frame rate measured from arrival times">Source</th>
          <th title="Arrival gaps of the last frames in ms, like vMix's Video column">Video (ms)</th>
          <th title="Frames NDI reports as dropped since the last Reset">Source dropped</th>
          <th title="Frames that arrived while the detector was still busy">Detector skipped</th>
          <th>Detections</th><th>Detect time p50 / p95</th><th title="Frame arrival to detection result">Frame → result</th>
          <th title="VISCA command to camera acknowledgement">VISCA round trip</th><th>VISCA errors</th>
          <th>FreeD</th><th>Targets lost</th><th>Re-found</th><th>Watchdog stops</th>
        </tr>
      </thead>
      <tbody>
        {#each cameras as c (c.id)}
          {@const k = (n: string) => stat(sample, c.id, n)}
          <tr>
            <td>{c.name}</td>
            <td><span class="badge state-{c.state}">{c.state.replace("_", " ")}</span></td>
            <td>{fmt(k("source_fps"))} fps</td>
            <td>{(c.source?.gaps_ms ?? []).join(" ") || "–"}</td>
            <td>{c.ndi_dropped} / {c.ndi_total}</td>
            <td>{fmt(k("skipped_frames_per_s"))}/s</td>
            <td>{fmt(k("detections_per_s"))}/s</td>
            <td>{fmt(k("detect_ms_p50"))} / {fmt(k("detect_ms_p95"))} ms</td>
            <td>{fmt(k("pipeline_ms_p50"))} / {fmt(k("pipeline_ms_p95"))} ms</td>
            <td>{fmt(k("visca_rtt_ms_p50"), 2)} / {fmt(k("visca_rtt_ms_max"), 2)} ms</td>
            <td>{fmt(k("visca_errors_per_s") ?? 0)}/s</td>
            <td>{fmt(k("freed_packets_per_s"), 0)}/s, {fmt(c.freed_age * 1000, 0)} ms old</td>
            <td>{c.counts.targets_lost}</td>
            <td>{c.counts.refinds}</td>
            <td>{c.counts.watchdog_stops}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <h2 class="section">PC</h2>
  <div class="grid">
    <div class="kv mono">
      <div><span>CPU, whole PC</span><span>{fmt(sample?.cpu_percent, 0)}%</span></div>
      <div><span>CPU, TrackXen</span><span>{fmt(sample?.app_cpu_percent)}%</span></div>
      <div><span>Memory, whole PC / TrackXen</span><span>{fmt(sample?.memory_percent, 0)}% / {sample?.app_memory_mb ?? "–"} MB</span></div>
      <div><span>GPU busy (NVIDIA) / TrackXen</span><span>{gpu ? `${gpu.load_percent}% / ${gpu.app_load_percent ?? 0}%` : "–"}</span></div>
      <div><span>GPU memory</span><span>{gpu ? `${fmt(gpu.memory_used_mb / 1024)} of ${fmt(gpu.memory_total_mb / 1024)} GB` : "–"}</span></div>
      <div><span>TrackXen video memory</span><span>{engines?.app_memory_mb ?? "–"} MB</span></div>
      <div><span>GPU temperature</span><span>{gpu ? `${gpu.temperature_c} °C` : "–"}</span></div>
      <div><span>3D / video engines (Windows; vMix shows here)</span>
        <span>{engines ? Object.entries(engines.engine_types).map(([n, v]) => `${n} ${fmt(v, 0)}%`).join(", ") || "idle" : "–"}</span></div>
      <div><span>Network in / out</span><span>{fmt(sample?.net_rx_mbps)} / {fmt(sample?.net_tx_mbps)} Mbit/s</span></div>
      <div><span>Control loop (95th)</span><span>{fmt(sample?.app?.["app.control_loop_ms_p95"], 2)} ms</span></div>
      <div><span>Engine</span><span>{live.status?.engine ?? "–"}</span></div>
    </div>
    <div class="graphs">
      <Sparkline label="CPU %" values={live.history.map((h) => h.cpu)} />
      <Sparkline label="GPU busy %" values={live.history.map((h) => h.gpu)} color="#3a8ee6" />
      <Sparkline label="Detect time ms" values={live.history.map((h) => h.detect)} max={40} color="#2ecc71" unit=" ms" />
    </div>
  </div>

  <h2 class="section">Problems</h2>
  {#each live.status?.problems ?? [] as p}
    <div class="problem {p.level}"><span class="light {p.level}"></span> {p.source}: {p.message}</div>
  {:else}
    <div class="dim">None</div>
  {/each}

  <h2 class="section">Log file <span class="dim path">{live.status?.log_file ?? ""}</span></h2>
  <pre>{logText}</pre>
  {/if}
</div>

<style>
  .stats { padding: 12px 16px; overflow-y: auto; height: 100%; }
  .head { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
  .head nav { display: flex; gap: 2px; }
  .head nav button { min-height: 30px; padding: 2px 14px; }
  h1 { font-size: 18px; margin: 0; }
  .scroll { overflow-x: auto; margin-top: 10px; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; white-space: nowrap; }
  th { text-align: left; color: var(--dim); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--line); cursor: help; }
  td { padding: 6px 8px; border-bottom: 1px solid #333; }
  .grid { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(260px, 1fr); gap: 16px; }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  .kv div { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; border-bottom: 1px solid #333; font-size: 13px; }
  .kv span:first-child { color: var(--dim); }
  .graphs { display: flex; flex-direction: column; gap: 8px; }
  .problem.warning { color: var(--yellow); }
  .problem.error { color: #ff6b5b; }
  .path { font-weight: 400; font-size: 11px; }
  pre { background: #111; border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; font-size: 11px;
        max-height: 320px; overflow: auto; white-space: pre-wrap; color: #cfcfcf; }
</style>

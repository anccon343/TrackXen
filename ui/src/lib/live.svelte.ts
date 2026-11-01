// Live connection to the TrackXen engine: status (10 Hz) and preview pictures over one WebSocket.
import { access } from "./access.svelte";

export interface Track {
  id: number;
  box: [number, number, number, number];
  score: number;
  keypoints: [number, number, number][];
  velocity: [number, number];
  missed: number;
  age: number;
  face?: { id: string; score: number }; // a registered face, when recognised
}

export interface Framing {
  anchor_x: number;
  anchor_y: number;
  dead_zone_x: number;
  dead_zone_y: number;
  display_size: boolean;
  auto_zoom: boolean;
  target_height: number;
  zoom_dead_band: number;
}

export type AreaKind = "range" | "initial" | "priority" | "fixed" | "auto_select" | "exclusion" | "halting";

/** An area of the active App Settings, projected into the current picture (0..1, may reach outside). */
export interface AreaInView {
  kind: AreaKind;
  index: number;
  rect: [number, number, number, number];
}

export type TrackingState = "idle" | "no_target" | "searching" | "tracking" | "angle_fixed" | "lost" | "no_video";

export interface CameraStatus {
  id: string;
  name: string;
  state: TrackingState;
  tracking: boolean;
  target: number | null;
  engine: string;
  frame: { width: number; height: number };
  tracks: Track[];
  pose: { pan: number; tilt: number; zoom: number; hfov: number } | null;
  command: [number, number, number];
  camera_ok: boolean;
  freed_age: number;
  source: { gaps_ms?: number[]; connected?: boolean; source?: string };
  framing: Framing;
  halting_area: boolean;
  counts: { targets_lost: number; refinds: number; watchdog_stops: number; manual_operations: number };
  ndi_dropped: number;
  ndi_total: number;
  slot: number;
  slot_names: string[];
  settings_rev: number;
  paused: boolean;
  moving: string;
  fixed: number | null;
  zones: Record<string, "halting" | "excluded">;
  auto_select_wait: Record<string, number>;
  areas: AreaInView[];
  problems: string[];
  group: number[]; // people framed with the target (Auto Framing: Multi-Person)
  facing: "" | "left" | "right" | "forward";
  posture: "" | "sitting" | "standing";
  target_face: string | null;
  faces: { id: string; enabled: boolean }[];
  face_guide: { ok: boolean; problem: string; box: [number, number, number, number] | null } | null;
  face_capturing: boolean;
  on_air: boolean; // on Program: vMix tally, or the manual switch
  on_air_tally: boolean;
  on_air_manual: boolean | null; // null = follow vMix
  air: number; // 0..1 blend towards the on-air motion
  audio_follow: boolean;
  cue: { label: string; channel: number } | null;
}

export interface VmixState {
  host: string;
  connected: boolean;
  error: string;
  inputs: { number: number; key: string; title: string; type: string; tally: number }[];
  suggested: Record<string, string>; // camera -> input key matching its NDI name
}

export interface MixerState {
  host: string;
  connected: boolean;
  error: string;
  channels: { channel: number; name: string; level: number; muted: boolean; speaking: boolean }[];
}

export interface Reply {
  ok: boolean;
  error?: string;
  message?: string;
  target?: number | null;
  problems?: string[];
  rev?: number;
  [key: string]: unknown;
}

export interface Gpu {
  name: string;
  load_percent: number;
  app_load_percent?: number;
  memory_used_mb: number;
  memory_total_mb: number;
  temperature_c: number;
}

export interface Sample {
  cpu_percent: number;
  memory_percent: number;
  app_cpu_percent: number;
  app_memory_mb: number;
  net_rx_mbps: number;
  net_tx_mbps: number;
  gpus: Gpu[];
  gpu_engines: { adapter: string; load_percent: number; engine_types: Record<string, number>; app_memory_mb: number }[];
  app: Record<string, number>;
}

export interface Problem {
  level: "warning" | "error";
  source: string;
  message: string;
}

export interface Status {
  engine: string;
  worker_alive: boolean;
  cameras: CameraStatus[];
  stats: Sample | Record<string, never>;
  problems: Problem[];
  log_file: string;
  hwtest_running: boolean;
  performance: { mode: string; preset: string; model: string; imgsz: number };
  vmix: VmixState | null;
  mixer: MixerState | null;
  audio_enabled: boolean;
  engine_building: boolean; // TensorRT being prepared for this GPU (first start)
  engine_building_s: number;
  gpu_libs: GpuLibs; // NVIDIA's libraries, downloaded on the first start of the installed app
}

export interface GpuLibs {
  state: "not_needed" | "checking" | "downloading" | "ready" | "failed";
  done_mb?: number;
  total_mb?: number;
  file?: string;
  error?: string;
  folder?: string;
}

export interface HistoryPoint {
  t: number;
  cpu: number;
  gpu: number;
  detect: number;
}

class Live {
  status = $state<Status | null>(null);
  connected = $state(false);
  /** Bumped whenever a new preview picture arrives (canvases redraw on their own animation loop). */
  previewTick = $state(0);
  history = $state<HistoryPoint[]>([]);
  previews: Record<string, ImageBitmap> = {};
  private ws: WebSocket | null = null;
  private lastStats = "";
  private nextId = 1;
  private pending = new Map<number, (reply: Reply) => void>();

  connect(): void {
    if (this.ws) return; // already connected or connecting
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => (this.connected = true);
    ws.onclose = (ev) => {
      this.connected = false;
      this.ws = null;
      if (ev.code === 4401) access.lost(); // the PIN is needed: the PIN screen connects again
      else setTimeout(() => this.connect(), 1000);
    };
    ws.onmessage = (ev) => this.onMessage(ev);
    this.ws = ws;
  }

  private async onMessage(ev: MessageEvent): Promise<void> {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "status") {
        this.status = msg as Status;
        this.recordHistory(msg as Status);
      } else if (msg.type === "reply" && msg.request) {
        this.pending.get(msg.request)?.(msg as Reply);
        this.pending.delete(msg.request);
      }
      return;
    }
    const bytes = new Uint8Array(ev.data as ArrayBuffer);
    const nl = bytes.indexOf(10);
    const camera = new TextDecoder().decode(bytes.slice(0, nl));
    const bitmap = await createImageBitmap(new Blob([bytes.slice(nl + 1)], { type: "image/jpeg" }));
    this.previews[camera]?.close();
    this.previews[camera] = bitmap;
    this.previewTick++;
  }

  /** Keep the last 3 minutes of once-a-second samples for the statistics graphs. */
  private recordHistory(s: Status): void {
    const sample = s.stats as Sample;
    if (!sample || !("cpu_percent" in sample)) return;
    const key = JSON.stringify([sample.cpu_percent, sample.app]);
    if (key === this.lastStats) return; // samples change once a second; status comes 10x a second
    this.lastStats = key;
    const detect = Math.max(0, ...Object.entries(sample.app).filter(([k]) => k.endsWith(".detect_ms_p50")).map(([, v]) => v));
    this.history = [...this.history.slice(-179), {
      t: Date.now(), cpu: sample.cpu_percent, gpu: sample.gpus[0]?.load_percent ?? 0, detect,
    }];
  }

  send(cmd: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(cmd));
  }

  /** Send a command and wait for the engine's answer ({ok: false, error} on failure). */
  request(cmd: Record<string, unknown>): Promise<Reply> {
    if (this.ws?.readyState !== WebSocket.OPEN) return Promise.resolve({ ok: false, error: "not connected" });
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.send({ ...cmd, id });
      setTimeout(() => {
        if (this.pending.delete(id)) resolve({ ok: false, error: "no answer from TrackXen" });
      }, 5000);
    });
  }
}

export const live = new Live();

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (r.status === 401) access.lost();
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 401) access.lost();
  if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`);
  return r.json();
}

export const fmt = (v: number | undefined | null, digits = 1): string =>
  v === undefined || v === null || Number.isNaN(v) ? "–" : Number(v).toFixed(digits);

/** App counter for one camera, e.g. stat(sample, "cam1", "detect_ms_p50"). */
export function stat(sample: Sample | Record<string, never> | undefined, camera: string, name: string): number | undefined {
  return sample && "app" in sample ? (sample as Sample).app[`${camera}.${name}`] : undefined;
}

// Camera types (engine/trackxen/core/camera_models.py): BirdDog P400, PTZOptics G2 20X and 12X, other VISCA cameras.
import { getJSON, postJSON } from "./live.svelte";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Camera = Record<string, any>;

export interface CameraType {
  name: string;
  about: string;
  estimate: string[]; // values from the data sheet, to be measured with the lab tools
  defaults: Camera;
}

// A network stream that brings fewer pictures than this a second: the camera can't keep up with its own stream
// (a PTZOptics G2 at 12 Mbps brings 18-21 a second, seconds late; at 4 Mbps 25).
const SLOW_STREAM_FPS = 22;

export interface CameraTest {
  visca: { ok: boolean; error?: string; rtt_ms?: number; pan?: number; tilt?: number; zoom?: number } | null;
  video: { ok: boolean; error?: string; url?: string; width?: number; height?: number; codec?: string; fps?: number;
           decode_ms?: number | null; damaged?: number } | null;
}

export const VISCA_CONNECTIONS: Record<string, { label: string; port: number }> = {
  sony: { label: "Sony VISCA over IP (UDP, BirdDog)", port: 52381 },
  udp: { label: "Plain VISCA over UDP (PTZOptics)", port: 1259 },
  tcp: { label: "Plain VISCA over TCP (PTZOptics)", port: 5678 },
};

class CameraTypes {
  list = $state<Record<string, CameraType>>({});

  async load(): Promise<void> {
    if (!Object.keys(this.list).length) this.list = await getJSON("/api/camera-models");
  }

  /** A new camera of this type (number n: its id, name and FreeD port). */
  create(model: string, n: number, host = ""): Camera {
    const d = structuredClone($state.snapshot(this.list[model]?.defaults ?? {}));
    if (d.freed_port) d.freed_port = 40000 + n;
    return { id: `cam${n}`, name: `Camera ${n}`, ndi: "", capture_device: 0, low_bandwidth: false, host, ...d, model };
  }

  /** A new camera of this type next to `cams`: the first free number (id camN) and a free FreeD port. */
  add(model: string, cams: Camera[]): Camera {
    let n = 1;
    while (cams.some((c) => c.id === `cam${n}`)) n++;
    const cam = this.create(model, n);
    if (cam.freed_port) cam.freed_port = freePort(cams, cam.freed_port);
    return cam;
  }

  /** Switch a camera to another type: its type's control, lens, speed and video settings. Its FreeD
   *  port stays (the camera sends to it); one is picked when it had none. */
  apply(cam: Camera, model: string, cams: Camera[]): void {
    const keep = { id: cam.id, name: cam.name, host: cam.host, ndi: cam.ndi, capture_device: cam.capture_device };
    const port = cam.freed_port;
    Object.assign(cam, this.create(model, 1), keep);
    if (cam.freed_port) cam.freed_port = port || freePort(cams.filter((c) => c !== cam), 40001);
  }

  /** The RTSP address a camera uses when none is typed (PTZOptics: rtsp://IP:554/1). */
  defaultUrl(cam: Camera, sub = false): string {
    return cam.host ? `rtsp://${cam.host}:554/${sub ? 2 : 1}` : "";
  }

  test(cam: Camera): Promise<CameraTest> {
    return postJSON<CameraTest>("/api/camera-test", $state.snapshot(cam));
  }
}

export const cameraTypes = new CameraTypes();

/** The first FreeD port from `from` up that none of `cams` uses. */
function freePort(cams: Camera[], from: number): number {
  let port = from;
  while (cams.some((c) => c.freed_port === port)) port++;
  return port;
}

/** One line for the user about a camera test. */
export function describeTest(t: CameraTest): string[] {
  const out: string[] = [];
  if (t.visca) out.push(t.visca.ok
    ? `VISCA answers in ${t.visca.rtt_ms} ms: pan ${t.visca.pan}°, tilt ${t.visca.tilt}°, zoom ${t.visca.zoom}`
    : `VISCA: no answer (${t.visca.error})`);
  if (t.video) out.push(t.video.ok
    ? `Video: ${t.video.width}×${t.video.height} ${t.video.codec?.toUpperCase()}, ${t.video.fps} fps, decoding ${t.video.decode_ms} ms per frame` +
      (t.video.damaged ? `, ${t.video.damaged} damaged frames` : "")
    : `Video: ${t.video.error}`);
  if (t.video?.ok && t.video.url && (t.video.fps ?? 0) < SLOW_STREAM_FPS)
    out.push("The camera can't keep up with its stream (its picture arrives late). In the camera's web page, " +
             "set the main stream to 4 Mbps CBR and restart the camera (user guide: PTZOptics cameras).");
  return out;
}

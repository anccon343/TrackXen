// Camera types (engine/trackxen/core/camera_models.py): BirdDog P400, PTZOptics G2, other VISCA cameras.
import { getJSON, postJSON } from "./live.svelte";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Camera = Record<string, any>;

export interface CameraType {
  name: string;
  about: string;
  estimate: string[]; // values from the data sheet, to be measured with the lab tools
  defaults: Camera;
}

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

  /** Switch a camera to another type: its type's control, lens, speed and video settings. */
  apply(cam: Camera, model: string, n: number): void {
    const keep = { id: cam.id, name: cam.name, host: cam.host, ndi: cam.ndi, capture_device: cam.capture_device };
    Object.assign(cam, this.create(model, n), keep);
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
  return out;
}

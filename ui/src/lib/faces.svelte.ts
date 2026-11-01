// Registered faces (Canon step 9): the faces kept on the TrackXen PC, and each camera's list of
// them (priority order, on/off). Names show on the picture next to recognised people.
import { getJSON, live, postJSON, type Reply } from "./live.svelte";
import { ui } from "./ui.svelte";

export interface FaceInfo { id: string; name: string; created: number }
export interface FaceRef { id: string; enabled: boolean }

class Faces {
  list = $state<FaceInfo[]>([]);
  cameras = $state<Record<string, FaceRef[]>>({});
  /** Camera whose picture shows the registration frame, if any. */
  guide = $state<string | null>(null);
  version = $state(0); // bumps thumbnails' URLs after changes
  private loaded = false;

  name(id: string | undefined): string {
    return (id && this.list.find((f) => f.id === id)?.name) || "";
  }

  async load(): Promise<void> {
    const data = await getJSON<{ faces: FaceInfo[]; cameras: Record<string, FaceRef[]> }>("/api/faces");
    this.list = data.faces;
    this.cameras = data.cameras;
    this.loaded = true;
    this.version++;
  }

  ensure(): void {
    if (!this.loaded) this.load();
  }

  async command(camera: string, cmd: Record<string, unknown>): Promise<Reply> {
    const reply = await live.request({ camera, ...cmd });
    if (!reply.ok) ui.notify(reply.error ?? "Not possible");
    await this.load();
    return reply;
  }

  async startGuide(camera: string): Promise<void> {
    this.guide = camera;
    await live.request({ type: "face_guide", camera, on: true });
  }

  async stopGuide(): Promise<void> {
    if (this.guide) await live.request({ type: "face_guide", camera: this.guide, on: false });
    this.guide = null;
  }

  async wipe(): Promise<void> {
    await postJSON("/api/faces/wipe", {});
    await this.load();
  }
}

export const faces = new Faces();

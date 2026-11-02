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
  private retryTimer: ReturnType<typeof setTimeout> | null = null; // stopGuide trying again
  private turn = 0;

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
    this.newTurn();
    this.guide = camera;
    await live.request({ type: "face_guide", camera, on: true });
  }

  /** Switch the registration frame off. The engine keeps it on (over that camera's picture on every
   *  device) until this arrives, so it goes over HTTP while the live connection is down, and is sent
   *  again every 2 s until it gets through. */
  async stopGuide(retry = false): Promise<void> {
    const camera = this.guide;
    const turn = this.newTurn();
    if (!camera) return;
    const reply = await live.requestReliably({ type: "face_guide", camera, on: false });
    if (turn !== this.turn) return; // started or stopped again meanwhile
    if (reply.ok || reply.error === "unknown camera") {
      this.guide = null;
      return;
    }
    if (!retry) ui.notify(`The registration frame is still on (${reply.error ?? "no answer"}): trying again`);
    this.retryTimer = setTimeout(() => this.stopGuide(true), 2000);
  }

  /** A newer start or stop of the frame: an older one's late answer or retry no longer counts. */
  private newTurn(): number {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    return ++this.turn;
  }

  async wipe(): Promise<void> {
    await postJSON("/api/faces/wipe", {});
    await this.load();
  }
}

export const faces = new Faces();

// The page closed or reloaded while registering: switch the frame off on the way out.
addEventListener("pagehide", () => {
  if (faces.guide) void live.requestReliably({ type: "face_guide", camera: faces.guide, on: false }, true);
});

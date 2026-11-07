// Updates from GitHub (Settings → System → Updates): engine/trackxen/core/updates.py does the work.
import { getJSON, HttpError, postJSON } from "./live.svelte";

export interface UpdateStatus {
  current: { version: string; build: number; commit: string };
  installed: boolean; // the installed Windows program (a development copy can check, not install)
  token_set: boolean;
  state: "idle" | "checking" | "downloading" | "ready" | "error";
  error: string;
  latest: { tag: string; build: number; name: string; published: string; notes: string; file: string; size: number } | null;
  newer: boolean;
  received: number;
}

/** The engine's own words from an error answer ({"detail": "..."}). */
function detail(e: unknown): string {
  if (e instanceof HttpError) {
    try { return JSON.parse(e.body).detail ?? e.message; } catch { return e.body || e.message; }
  }
  return String(e);
}

class Updates {
  status = $state<UpdateStatus | null>(null);
  message = $state("");
  busy = $state(false);
  private timer: ReturnType<typeof setInterval> | null = null;

  async load(): Promise<void> {
    try { this.status = await getJSON<UpdateStatus>("/api/update"); } catch (e) { this.message = detail(e); }
  }

  private async post(url: string, body: unknown = {}): Promise<boolean> {
    this.busy = true;
    this.message = "";
    try {
      this.status = await postJSON<UpdateStatus>(url, body);
      return true;
    } catch (e) {
      this.message = detail(e);
      return false;
    } finally {
      this.busy = false;
    }
  }

  saveToken(token: string) { return this.post("/api/update/token", { token }); }
  check() { return this.post("/api/update/check"); }

  async download(): Promise<void> {
    if (!(await this.post("/api/update/download"))) return;
    this.watch();
  }

  /** Follow a download until it has finished. */
  watch(): void {
    if (this.timer) return;
    this.timer = setInterval(async () => {
      await this.load();
      if (this.status?.state !== "downloading") this.unwatch();
    }, 1000);
  }

  unwatch(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async install(): Promise<boolean> {
    this.busy = true;
    this.message = "";
    try {
      await postJSON("/api/update/install", {});
      this.message = "Installing: TrackXen closes now and starts again in about a minute. Reload this page then.";
      return true;
    } catch (e) {
      this.message = detail(e);
      return false;
    } finally {
      this.busy = false;
    }
  }
}

export const updates = new Updates();

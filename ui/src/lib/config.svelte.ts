// Settings as stored by the engine (engine/trackxen/core/config.py), edited by the settings pages.
import { getJSON, HttpError, postJSON } from "./live.svelte";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Config = Record<string, any>;

class Settings {
  saved = $state<Config | null>(null);
  draft = $state<Config | null>(null);
  busy = $state(false);
  error = $state("");

  /** The engine's settings, with "_rev": which version they are (sent back with Apply). */
  async load(): Promise<void> {
    const cfg = await getJSON<Config>("/api/config");
    this.saved = cfg;
    this.draft = structuredClone(cfg);
  }

  get dirty(): boolean {
    return JSON.stringify(this.saved) !== JSON.stringify(this.draft);
  }

  changed(path: (c: Config) => unknown): boolean {
    if (!this.saved || !this.draft) return false;
    return JSON.stringify(path(this.saved)) !== JSON.stringify(path(this.draft));
  }

  /** Save and apply: the engine stops the cameras, then restarts them with the new settings.
   *  False (and `error` says why) when they were not applied. */
  async apply(): Promise<boolean> {
    if (!this.draft) return false;
    this.error = problems(this.draft);
    if (this.error) return false;
    this.busy = true;
    try {
      await postJSON("/api/config", this.draft);
      await this.load();
      return true;
    } catch (e) {
      if (e instanceof HttpError && e.status === 409) {
        // another device applied settings since this page loaded them: show theirs
        this.error = detail(e.body) || "The settings were changed on another device. Reload them and try again.";
        await this.load().catch(() => undefined);
      } else {
        this.error = String(e);
      }
      return false;
    } finally {
      this.busy = false;
    }
  }

  clear(): void {
    if (this.saved) this.draft = structuredClone($state.snapshot(this.saved));
  }
}

export const settings = new Settings();

const isPort = (v: unknown): boolean => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 65535;

/** What the engine would refuse in these settings ("" if nothing): an emptied or wrong port, or two
 *  cameras with the same id or FreeD port. */
function problems(cfg: Config): string {
  const cams: Config[] = cfg.cameras ?? [];
  for (const [i, c] of cams.entries()) {
    if (!isPort(c.visca_port)) return `Camera ${i + 1}: the VISCA port must be a number from 1 to 65535.`;
    if (c.freed_port != null && !isPort(c.freed_port))
      return `Camera ${i + 1}: the FreeD port must be a number from 1 to 65535, or empty without FreeD.`;
  }
  const ids = cams.map((c) => c.id);
  if (new Set(ids).size !== ids.length) return "Two cameras have the same id: remove one of them and add it again.";
  const ports = cams.map((c) => c.freed_port).filter((p) => p != null);
  if (new Set(ports).size !== ports.length) return "Two cameras use the same FreeD port: give each camera its own.";
  return "";
}

/** The message in an engine error answer ({"detail": "..."}). */
function detail(body: string): string {
  try {
    const d = JSON.parse(body).detail;
    return typeof d === "string" ? d : "";
  } catch {
    return "";
  }
}

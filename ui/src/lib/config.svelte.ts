// Settings as stored by the engine (engine/trackxen/core/config.py), edited by the settings pages.
import { getJSON, postJSON } from "./live.svelte";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Config = Record<string, any>;

class Settings {
  saved = $state<Config | null>(null);
  draft = $state<Config | null>(null);
  busy = $state(false);
  error = $state("");

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

  /** Save and apply: the engine stops the cameras, then restarts them with the new settings. */
  async apply(): Promise<void> {
    if (!this.draft) return;
    this.busy = true;
    this.error = "";
    try {
      await postJSON("/api/config", this.draft);
      await this.load();
    } catch (e) {
      this.error = String(e);
    } finally {
      this.busy = false;
    }
  }

  clear(): void {
    if (this.saved) this.draft = structuredClone($state.snapshot(this.saved));
  }
}

export const settings = new Settings();

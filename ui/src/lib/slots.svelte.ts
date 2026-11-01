// The selected camera's App Settings (Canon's 5 tracking profiles), as the engine keeps them
// (engine/trackxen/core/tracking_settings.py). Changes go to the engine at once and are live;
// "save" keeps them on disk.
import { getJSON, live, type CameraStatus, type Framing, type Reply } from "./live.svelte";
import { ui } from "./ui.svelte";

export interface View { set: boolean; pan: number; tilt: number; zoom: number }
export interface Area { enabled: boolean; left: number; right: number; top: number; bottom: number; view: View }

export interface AppSettings {
  name: string;
  fix_tilt: boolean;
  shooting_range: { enabled: boolean; left: number; right: number; top: number; bottom: number };
  initial: View;
  lost_action: "maintain" | "return";
  return_after: number;
  priority_area: Area;
  fixed_areas: Area[];
  auto_select: { enabled: boolean; start_time: number; area: Area };
  restart_after_manual: boolean;
  composition: Framing;
  halting_area: boolean;
  sensitivity: number;
  exclusion_areas: Area[];
  halting_areas: Area[];
  auto_framing: {
    enabled: boolean;
    multi_person: { enabled: boolean; zoom_speed: number; min_size: number; transition_speed: number };
    face_direction: { enabled: boolean; sensitivity: number; space: number; forward: "maintain" | "center" };
    sit_stand: { enabled: boolean; sit_size: number; stand_size: number };
  };
  registered_faces: boolean;
  on_air: { enabled: boolean; speed: number; accel: number; dead_zone: number; zoom: "slow" | "off"; retarget: "wait" | "slow" };
}

export interface PresetLink { auto_tracking: "" | "on" | "off"; slot: number }

interface SlotsData {
  active: number;
  rev: number;
  slots: AppSettings[];
  preset_links: Record<string, PresetLink>;
  problems: string[][];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Patch = Record<string, any>;

/** Deep-merge like the engine: lists of areas are patched by index ({"2": {...}}). */
function mergeInto(target: Patch, patch: Patch): void {
  for (const [key, value] of Object.entries(patch)) {
    const current = target[key];
    if (Array.isArray(current) && value && typeof value === "object" && !Array.isArray(value)) {
      for (const [i, item] of Object.entries(value)) mergeInto(current[Number(i)], item as Patch);
    } else if (current && typeof current === "object" && value && typeof value === "object" && !Array.isArray(value)) {
      mergeInto(current, value as Patch);
    } else {
      target[key] = value;
    }
  }
}

class Slots {
  camera = $state("");
  data = $state<SlotsData | null>(null);
  private inFlight = 0;
  private loading = false;

  get slot(): AppSettings | undefined {
    return this.data?.slots[this.data.active];
  }

  get problems(): string[] {
    return this.data?.problems[this.data.active] ?? [];
  }

  /** Follow the selected camera; reload when someone else (another tablet) changed its settings. */
  sync(camera: CameraStatus | undefined): void {
    if (!camera) return;
    if (camera.id !== this.camera) this.load(camera.id);
    else if (this.data && camera.settings_rev > this.data.rev && !this.inFlight) this.load(camera.id);
  }

  async load(camera = this.camera): Promise<void> {
    if (this.loading || !camera) return;
    this.loading = true;
    try {
      this.data = await getJSON<SlotsData>(`/api/slots/${encodeURIComponent(camera)}`);
      this.camera = camera;
    } catch (e) {
      ui.notify(`Couldn't load tracking settings: ${e}`);
    } finally {
      this.loading = false;
    }
  }

  /** Change values of the selected App Settings: shown at once, then confirmed by the engine. */
  async patch(settings: Patch): Promise<Reply> {
    const slot = this.slot;
    if (slot) mergeInto(slot as unknown as Patch, settings);
    return this.command({ type: "slot_settings", settings }, false);
  }

  /** A camera command that changes settings; reloads them afterwards when the result is only known there. */
  async command(cmd: Patch, reload = true): Promise<Reply> {
    this.inFlight++;
    try {
      const reply = await live.request({ camera: this.camera, ...cmd });
      if (!reply.ok) {
        ui.notify(reply.error ?? "Not possible");
        await this.load();
      } else if (reload) {
        await this.load();
      } else if (this.data) {
        if (reply.rev !== undefined) this.data.rev = reply.rev;
        if (reply.problems) this.data.problems[this.data.active] = reply.problems;
        // the engine's view of the settings (it may change related values, e.g. Sit/Stand); not
        // while more changes are on the way, or a slider being dragged would jump back
        if (reply.settings && this.inFlight === 1) this.data.slots[this.data.active] = reply.settings as AppSettings;
      }
      return reply;
    } finally {
      this.inFlight--;
    }
  }
}

export const slots = new Slots();

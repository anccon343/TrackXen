// Camera box sizes on the Cameras page, set by dragging a box's corner (CameraTile.svelte): each camera's box
// width in pixels. Kept in this browser only: a view preference of this device, not a TrackXen setting.
const KEY = "trackxen.tileSizes";

function load(): Record<string, number> {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return Object.fromEntries(Object.entries(saved).filter(([, px]) => typeof px === "number" && px > 0)) as
      Record<string, number>;
  } catch {
    return {}; // private window, blocked storage or a damaged value: automatic sizes
  }
}

class TileSizes {
  widths = $state<Record<string, number>>(load());

  /** Some box has a size of its own (the toolbar then offers "Auto size"). */
  get custom(): boolean {
    return Object.keys(this.widths).length > 0;
  }

  /** A camera's box width (while dragging; save() keeps it). */
  set(id: string, px: number): void {
    this.widths[id] = Math.round(px);
  }

  /** Back to the automatic size for one camera's box. */
  clear(id: string): void {
    delete this.widths[id];
    this.save();
  }

  /** Back to the automatic size for every box. */
  reset(): void {
    this.widths = {};
    this.save();
  }

  save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.widths));
    } catch {
      /* not kept: the size holds until the page is reloaded */
    }
  }
}

export const tileSizes = new TileSizes();

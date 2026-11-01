// Page-only state: the mouse mode of the pictures, the silhouette view, and short messages.

/** Canon's mouse modes (dropdown at the top-left of the video). */
export type MouseMode = "select" | "center" | "silhouette" | "area";

export const MOUSE_MODES: { id: MouseMode; label: string }[] = [
  { id: "select", label: "Tracking Target Select" },
  { id: "center", label: "Click Centering" },
  { id: "silhouette", label: "Silhouette Manipulation" },
  { id: "area", label: "Set Area" },
];

/** Canon's Status badge texts (plus "No video", ours). */
export const STATE_TEXT: Record<string, string> = {
  idle: "Off", no_target: "No target selected", searching: "Searching", tracking: "Tracking",
  angle_fixed: "Angle Fixed", lost: "Target lost", no_video: "No video",
};

class UiState {
  mouseMode = $state<MouseMode>("select");
  silhouette = $state(false); // Silhouette View (Single Target): never saved, like Canon's
  message = $state("");
  private timer: ReturnType<typeof setTimeout> | null = null;

  notify(message: string): void {
    this.message = message;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => (this.message = ""), 4000);
  }
}

export const ui = new UiState();

// The optional PIN (engine/trackxen/core/access.py): whether this device must sign in, and signing in.

export interface AccessState {
  enabled: boolean; // a PIN is set
  local: boolean; // this page runs on the TrackXen PC itself
  ask_local: boolean; // ...and the PIN is asked there too
  signed_in: boolean;
  devices: number; // signed-in devices
  needs_pin: boolean; // this device must enter it now
  wait_s: number; // after too many wrong PINs
}

class Access {
  state = $state<AccessState | null>(null);
  /** True while the PIN screen is shown instead of the app. */
  needed = $state(false);

  async check(): Promise<void> {
    try {
      const r = await fetch("/api/access");
      if (!r.ok) return;
      this.state = await r.json();
      this.needed = this.state!.needs_pin;
    } catch {
      // TrackXen not reachable: the live connection shows that
    }
  }

  /** Called when the engine answers 401 (or closes the live connection with 4401). */
  lost(): void {
    this.needed = true;
  }

  async signIn(pin: string): Promise<string> {
    let r: Response;
    try {
      r = await fetch("/api/access/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin }),
      });
    } catch {
      return "TrackXen is not reachable: try again in a moment";
    }
    if (r.ok) {
      this.needed = false;
      await this.check();
      return "";
    }
    await this.check(); // the PIN may have been switched off meanwhile: then nothing more is needed
    if (!this.needed) return "";
    try {
      return (await r.json()).detail ?? "Wrong PIN";
    } catch {
      return "Wrong PIN";
    }
  }

  async post(url: string, body: Record<string, unknown>): Promise<string> {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.ok) {
      await this.check();
      return "";
    }
    try {
      const detail = (await r.json()).detail;
      return typeof detail === "string" ? detail : "Not accepted";
    } catch {
      return `Not accepted (${r.status})`;
    }
  }
}

export const access = new Access();

// Hold-to-move buttons: the "manual" command repeats while a button is held. The hold ends, and the
// camera is stopped, when the button is let go or loses the pointer, when the button leaves the page
// (its panel closed, or the page replaced), when the page is hidden or loses focus, and when the live
// connection drops (the stop then goes over HTTP). The engine also stops it if the repeats stop arriving.
import { live } from "./live.svelte";

let timer: ReturnType<typeof setInterval> | null = null;
let held = "";
let button: HTMLElement | null = null;
let pointer = -1;

function onPointerEnd(e: PointerEvent): void {
  if (e.pointerId === pointer) release();
}

function onVisibility(): void {
  if (document.hidden) release();
}

/** End the hold, if any, and stop the camera. */
function release(): void {
  if (timer === null) return;
  clearInterval(timer);
  timer = null;
  button = null;
  removeEventListener("pointerup", onPointerEnd, true);
  removeEventListener("pointercancel", onPointerEnd, true);
  document.removeEventListener("lostpointercapture", onPointerEnd, true);
  removeEventListener("blur", release);
  document.removeEventListener("visibilitychange", onVisibility);
  void live.requestReliably({ type: "manual", camera: held, pan: 0, tilt: 0, zoom: 0 });
}

/** Pointer handlers for a button that moves `camera()` by `speeds()` (signed pan, tilt, zoom steps). */
export function holdToMove(camera: () => string, speeds: () => [number, number, number]) {
  const send = (pan: number, tilt: number, zoom: number) => live.send({ type: "manual", camera: held, pan, tilt, zoom });
  return {
    onpointerdown: (e: PointerEvent) => {
      e.preventDefault();
      release();
      button = e.currentTarget as HTMLElement;
      pointer = e.pointerId;
      button.setPointerCapture(e.pointerId);
      held = camera();
      // also when the button's own handlers can't run any more (a button removed from the page gets
      // neither pointerup nor lostpointercapture: the browser sends those to the document)
      addEventListener("pointerup", onPointerEnd, true);
      addEventListener("pointercancel", onPointerEnd, true);
      document.addEventListener("lostpointercapture", onPointerEnd, true);
      addEventListener("blur", release);
      document.addEventListener("visibilitychange", onVisibility);
      send(...speeds());
      timer = setInterval(() => {
        if (!button?.isConnected || !live.connected) release();
        else send(...speeds());
      }, 150);
    },
    onpointerup: release,
    onpointercancel: release,
    onlostpointercapture: release,
  };
}

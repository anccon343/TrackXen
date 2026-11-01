// Hold-to-move buttons: the "manual" command repeats while a button is held; letting go (or
// losing the pointer) stops the camera. The engine also stops it if the repeats stop arriving.
import { live } from "./live.svelte";

let timer: ReturnType<typeof setInterval> | null = null;
let held = "";

/** Pointer handlers for a button that moves `camera()` by `speeds()` (signed pan, tilt, zoom steps). */
export function holdToMove(camera: () => string, speeds: () => [number, number, number]) {
  const send = (pan: number, tilt: number, zoom: number) => live.send({ type: "manual", camera: held, pan, tilt, zoom });
  const release = () => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
    send(0, 0, 0);
  };
  return {
    onpointerdown: (e: PointerEvent) => {
      e.preventDefault();
      release();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      held = camera();
      send(...speeds());
      timer = setInterval(() => send(...speeds()), 150);
    },
    onpointerup: release,
    onpointercancel: release,
    onlostpointercapture: release,
  };
}

// How each kind of area looks, after the colours and line styles in Canon's area diagram.
import type { AreaKind } from "./live.svelte";

export interface AreaStyle {
  colour: string;
  dash: number[]; // in CSS pixels; [] = solid
  label: string;
}

const FIXED = ["#f1c40f", "#2ecc71", "#3a8ee6", "#ff6fb5"]; // yellow, green, blue, pink as in Canon's screens
const EXCLUSION = ["#a569bd", "#c39bd3", "#8e44ad", "#d2b4de"];
const HALTING = ["#e040fb", "#ff4fd8", "#d81b60", "#f48fb1"];

export function areaStyle(kind: AreaKind, index = 0): AreaStyle {
  switch (kind) {
    case "range": return { colour: "#9e9e9e", dash: [], label: "Shooting range" };
    case "initial": return { colour: "#d9730d", dash: [], label: "Initial position" };
    case "priority": return { colour: "#3a8ee6", dash: [8, 5], label: "Priority display area" };
    case "fixed": return { colour: FIXED[index % 4], dash: [8, 5], label: `Fixed viewing angle ${index + 1}` };
    case "auto_select": return { colour: "#e74c3c", dash: [2, 4], label: "Auto select area" };
    case "exclusion": return { colour: EXCLUSION[index % 4], dash: [2, 4], label: `Exclusion ${index + 1}` };
    case "halting": return { colour: HALTING[index % 4], dash: [2, 4], label: `Tracking halting ${index + 1}` };
  }
}

/** Areas that can be dragged on the picture (the shooting range and initial position are captured instead). */
export const EDITABLE: AreaKind[] = ["priority", "fixed", "auto_select", "exclusion", "halting"];

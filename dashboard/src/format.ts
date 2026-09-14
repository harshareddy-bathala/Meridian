// Words for what the station directory says, kept apart from the components so
// they can be tested without rendering anything.

import type { Liveness } from "./stations";

const LIVENESS_LABEL: Record<Liveness, string> = {
  online: "Online",
  stale: "Stale",
  offline: "Offline",
  never_seen: "Never seen",
};

export function livenessLabel(liveness: Liveness): string {
  return LIVENESS_LABEL[liveness];
}

/** "just now", "42 s ago", "7 min ago", "3 h ago", "12 d ago"; "never" for null. */
export function formatAge(iso: string | null, now: Date): string {
  if (iso === null) {
    return "never";
  }
  const seconds = Math.round((now.getTime() - Date.parse(iso)) / 1000);
  if (Number.isNaN(seconds)) {
    return "unknown";
  }
  if (seconds < 5) {
    return "just now";
  }
  const steps: [number, string][] = [
    [60, "s"],
    [60, "min"],
    [24, "h"],
  ];
  let value = seconds;
  for (const [size, unit] of steps) {
    if (value < size) {
      return `${String(value)} ${unit} ago`;
    }
    value = Math.floor(value / size);
  }
  return `${String(value)} d ago`;
}

/** 2 decimals is roughly 1.1 km (D-082). Said in words so a map is not over-read. */
export function precisionLabel(decimals: number): string {
  const metres = 111_320 / 10 ** decimals;
  if (metres < 1) {
    return "to under 1 m";
  }
  return metres >= 1000
    ? `to about ${String(Math.round(metres / 1000))} km`
    : `to about ${String(Math.round(metres))} m`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function hhmm(instant: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(instant.getUTCHours())}:${pad(instant.getUTCMinutes())}`;
}

/**
 * "06:10–06:22 UTC, 14 Sep". Whole minutes, as the API publishes them (D-093).
 * Built by hand rather than with Intl: ICU versions disagree about month
 * abbreviations ("Sep" or "Sept"), and a time table should read the same in
 * every browser.
 */
export function formatWindow(startIso: string, endIso: string): string {
  const start = new Date(startIso);
  const month = MONTHS[start.getUTCMonth()] ?? "";
  return `${hhmm(start)}–${hhmm(new Date(endIso))} UTC, ${String(start.getUTCDate())} ${month}`;
}

/** "137.9 MHz". */
export function formatFrequency(hz: number): string {
  return `${String(Math.round(hz / 100_000) / 10)} MHz`;
}

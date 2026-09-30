// How much of each recent hour a station was heard from (D-178).
//
// One read, /api/v1/stations/{id}/uptime. Coverage, not evidence: whether a
// station was listening for a given pass is decided from raw heartbeats on the
// platform, and nothing drawn here stands in for that.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString } from "./decode";

export interface UptimeHour {
  hour: string;
  heartbeats: number;
  coverage: number;
}

export interface StationUptime {
  simulated: boolean;
  hours: UptimeHour[];
}

export function decodeUptime(body: unknown): StationUptime {
  const path = "uptime";
  const fields = asObject(body, path);
  const hours = fields.hours;
  if (!Array.isArray(hours)) {
    throw new Error(`${path}.hours: expected an array`);
  }
  return {
    simulated: asBoolean(fields, "simulated", path),
    hours: hours.map((value, index) => {
      const at = `${path}.hours[${String(index)}]`;
      const hour = asObject(value, at);
      return {
        hour: asString(hour, "hour", at),
        heartbeats: asNumber(hour, "heartbeats", at),
        coverage: asNumber(hour, "coverage", at),
      };
    }),
  };
}

export async function fetchUptime(fetcher: Fetcher, stationId: string, signal: AbortSignal): Promise<StationUptime> {
  const url = `/api/v1/stations/${encodeURIComponent(stationId)}/uptime?hours=48`;
  return decodeUptime(await getJson(fetcher, url, "uptime", signal));
}

/** One bar per hour, oldest on the left, as tall as the hour was covered. */
export function uptimeBars(
  hours: UptimeHour[],
  width: number,
  height: number,
): { x: number; y: number; width: number; height: number }[] {
  if (hours.length === 0) {
    return [];
  }
  const slot = width / hours.length;
  return hours.map((one, index) => {
    const coverage = Math.min(Math.max(one.coverage, 0), 1);
    const tall = coverage * height;
    return { x: index * slot, y: height - tall, width: Math.max(slot - 1, 1), height: tall };
  });
}

/** The share of the span the station was heard, averaged over its hours. */
export function averageCoverage(hours: UptimeHour[]): number {
  if (hours.length === 0) {
    return 0;
  }
  return hours.reduce((total, one) => total + one.coverage, 0) / hours.length;
}

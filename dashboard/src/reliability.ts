// One station's capture rate over the reliability window (D-184, D-186).
//
// One read, /api/v1/reliability, which states every figure for measured and for
// simulated stations apart (rule 5). The station is looked for in both, and the
// population it is found in is what it is labelled. A rate is a count over a
// count with its Wilson interval; one with nothing counted is said in words,
// never drawn as zero.

import { getJson, type Fetcher } from "./api";
import { asNumber, asObject, asString, DecodeError, type Fields } from "./decode";

export interface Proportion {
  numerator: number;
  denominator: number;
  /** Null when nothing was counted. */
  estimate: number | null;
  interval: [number, number] | null;
}

export interface StationCapture {
  simulated: boolean;
  capture: Proportion;
  windowStart: string;
  windowEnd: string;
}

function decodeInterval(fields: Fields, path: string): [number, number] | null {
  const value = fields.interval;
  if (value === null) {
    return null;
  }
  if (!Array.isArray(value) || value.length !== 2 || !value.every((one) => typeof one === "number")) {
    throw new DecodeError(`${path}.interval`, "a pair of numbers or null");
  }
  return [value[0] as number, value[1] as number];
}

export function decodeProportion(value: unknown, path: string): Proportion {
  const fields = asObject(value, path);
  return {
    numerator: asNumber(fields, "numerator", path),
    denominator: asNumber(fields, "denominator", path),
    estimate: fields.estimate === null ? null : asNumber(fields, "estimate", path),
    interval: decodeInterval(fields, path),
  };
}

/** The station's capture from the reliability body, or null if it was not counted. */
export function decodeStationCapture(body: unknown, stationId: string): StationCapture | null {
  const fields = asObject(body, "reliability");
  for (const population of ["measured", "simulated"] as const) {
    const path = `reliability.${population}`;
    const held = asObject(fields[population], path);
    const stations = held.stations;
    if (!Array.isArray(stations)) {
      throw new DecodeError(`${path}.stations`, "an array");
    }
    for (const [index, value] of stations.entries()) {
      const at = `${path}.stations[${String(index)}]`;
      const station = asObject(value, at);
      if (asString(station, "station_id", at) === stationId) {
        return {
          simulated: population === "simulated",
          capture: decodeProportion(station.capture, `${at}.capture`),
          windowStart: asString(fields, "window_start", "reliability"),
          windowEnd: asString(fields, "window_end", "reliability"),
        };
      }
    }
  }
  return null;
}

export async function fetchStationCapture(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<StationCapture | null> {
  const body = await getJson(fetcher, "/api/v1/reliability", "the reliability summary", signal);
  return decodeStationCapture(body, stationId);
}

/** "0.87 [0.79, 0.92], 41 of 47 passes", or why there is no rate. */
export function describeCapture(capture: Proportion): string {
  const { numerator, denominator, estimate, interval } = capture;
  if (estimate === null) {
    return "no pass settled in the window";
  }
  const passes = `${String(numerator)} of ${String(denominator)} ${denominator === 1 ? "pass" : "passes"}`;
  const span = interval === null ? "" : ` [${interval[0].toFixed(2)}, ${interval[1].toFixed(2)}]`;
  return `${estimate.toFixed(2)}${span}, ${passes}`;
}

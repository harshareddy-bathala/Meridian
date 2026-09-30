// A station's horizon: what its operator declared, and what its receptions say.
//
// One read, /api/v1/stations/{id}/profiles (D-174). The declared horizon is a
// claim and the learned one is evidence, so they are drawn apart and never
// merged (D-031). Only the declared one stops a pass being scheduled (D-175).
//
// The sky plot is pure geometry, tested without a browser: north up, east to
// the right, the zenith at the centre and the horizon at the edge.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString, type Fields } from "./decode";

export interface HorizonBin {
  azimuthDeg: number;
  widthDeg: number;
  minElevationDeg: number;
  /** Detections behind a learned bin; null for a declared one. */
  sampleCount: number | null;
}

export interface LearnedHorizon {
  method: string;
  trainedFrom: string;
  trainedUntil: string;
  bins: HorizonBin[];
}

export interface StationProfiles {
  stationId: string;
  simulated: boolean;
  /** One per capability that declares a mask; empty when none does. */
  declared: HorizonBin[][];
  /** Null until a labelled dataset has been built into a profile. */
  learned: LearnedHorizon | null;
}

function asArray(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new Error(`${path}.${key}: expected an array`);
  }
  return value;
}

function decodeBin(value: unknown, path: string): HorizonBin {
  const fields = asObject(value, path);
  const count = fields.sample_count;
  return {
    azimuthDeg: asNumber(fields, "azimuth_deg", path),
    widthDeg: asNumber(fields, "azimuth_width_deg", path),
    minElevationDeg: asNumber(fields, "min_elevation_deg", path),
    sampleCount: count === null || count === undefined ? null : asNumber(fields, "sample_count", path),
  };
}

function decodeBins(fields: Fields, path: string): HorizonBin[] {
  return asArray(fields, "bins", path).map((bin, index) => decodeBin(bin, `${path}.bins[${String(index)}]`));
}

export function decodeProfiles(body: unknown): StationProfiles {
  const path = "profiles";
  const fields = asObject(body, path);
  const learned = fields.learned;
  return {
    stationId: asString(fields, "station_id", path),
    simulated: asBoolean(fields, "simulated", path),
    declared: asArray(fields, "declared", path).map((one, index) =>
      decodeBins(asObject(one, `${path}.declared[${String(index)}]`), `${path}.declared[${String(index)}]`),
    ),
    learned:
      learned === null
        ? null
        : (() => {
            const learnedFields = asObject(learned, `${path}.learned`);
            return {
              method: asString(learnedFields, "method", `${path}.learned`),
              trainedFrom: asString(learnedFields, "trained_from", `${path}.learned`),
              trainedUntil: asString(learnedFields, "trained_until", `${path}.learned`),
              bins: decodeBins(learnedFields, `${path}.learned`),
            };
          })(),
  };
}

export async function fetchProfiles(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<StationProfiles> {
  const url = `/api/v1/stations/${encodeURIComponent(stationId)}/profiles`;
  return decodeProfiles(await getJson(fetcher, url, "profiles", signal));
}

/** Where an azimuth and elevation fall on a sky plot of ``radius`` about the origin. */
export function skyPoint(azimuthDeg: number, elevationDeg: number, radius: number): [number, number] {
  // Below the horizon is drawn at the edge: a floor of -90° declares no
  // obstruction, and the plot has no room outside its circle.
  const elevation = Math.min(Math.max(elevationDeg, 0), 90);
  const r = ((90 - elevation) / 90) * radius;
  const azimuth = (azimuthDeg * Math.PI) / 180;
  return [r * Math.sin(azimuth), -r * Math.cos(azimuth)];
}

/**
 * The outline of a horizon as an SVG path: each bin's floor held across its
 * width, sampled every few degrees so a wide bin is drawn as an arc.
 */
export function horizonPath(bins: HorizonBin[], radius: number, stepDeg = 5): string {
  if (bins.length === 0) {
    return "";
  }
  const points: [number, number][] = [];
  for (const bin of [...bins].sort((a, b) => a.azimuthDeg - b.azimuthDeg)) {
    const steps = Math.max(1, Math.ceil(bin.widthDeg / stepDeg));
    for (let step = 0; step <= steps; step += 1) {
      points.push(skyPoint(bin.azimuthDeg + (bin.widthDeg * step) / steps, bin.minElevationDeg, radius));
    }
  }
  const [first, ...rest] = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`);
  return `M${first ?? ""}${rest.map((point) => `L${point}`).join("")}Z`;
}

// The station planner's read, /api/v1/plan (D-370): the next 48 hours of passes
// over a place, each with its peak and configuration A's predicted yield.
//
// The place is the reader's. It is rounded to a tenth of a degree here, before
// it leaves the browser, and the platform rounds it again on arrival, so an
// exact address never travels. It is not put in the URL fragment either, so it
// stays out of the browser's history. The front forwards this read without
// keeping a copy, so it is not in snapshot-routes.txt and is never warmed.

import { getJson, type Fetcher } from "./api";
import {
  asNumber,
  asObject,
  asOneOf,
  asString,
  DecodeError,
  type Fields,
} from "./decode";

/** A tenth of a degree, about 11 km: what the platform uses (D-082, D-370). */
export const PLACE_DECIMALS = 1;

export interface Place {
  latDeg: number;
  lonDeg: number;
}

export type PlanYield = { probability: number } | { notMeasured: string };

export interface PlanPass {
  satelliteId: string;
  aos: string;
  los: string;
  maxElevationDeg: number;
  maxElevationAt: string;
  aosAzimuthDeg: number;
  losAzimuthDeg: number;
  predictedYield: PlanYield;
}

export interface PlanSatellite {
  satelliteId: string;
  name: string;
  downlinks: { centreFreqHz: number; mode: string }[];
  elementSetEpoch: string;
  elementSetAgeS: number;
  elementSetSource: string;
}

export interface PlanOmission {
  satelliteId: string;
  name: string;
  reason: "no_element_set" | "propagation_failed" | "over_limit";
}

export type YieldModel =
  | {
      configuration: "A";
      population: "own" | "archive";
      modelSha256: string;
      asOf: string;
    }
  | { notYetComputed: string };

export interface Plan {
  at: string;
  until: string;
  place: Place;
  minElevationDeg: number;
  yieldModel: YieldModel;
  satellites: PlanSatellite[];
  passes: PlanPass[];
  omitted: PlanOmission[];
}

/** The place as it is sent: to a tenth of a degree, never ``-0``. */
export function roundPlace(place: Place): Place {
  const round = (value: number) =>
    Math.round(value * 10 ** PLACE_DECIMALS) / 10 ** PLACE_DECIMALS + 0;
  return { latDeg: round(place.latDeg), lonDeg: round(place.lonDeg) };
}

/** What is wrong with a typed place, in words; ``null`` when it is a place. */
export function placeProblem(latText: string, lonText: string): string | null {
  if (!withinDegrees(latText, 90)) {
    return "Latitude is a number of degrees from -90 (south) to 90 (north).";
  }
  if (!withinDegrees(lonText, 180)) {
    return "Longitude is a number of degrees from -180 (west) to 180 (east).";
  }
  return null;
}

/** Whether typed text is a number of degrees no further than ``limit`` from 0. */
function withinDegrees(text: string, limit: number): boolean {
  const value = Number(text);
  return (
    text.trim() !== "" && Number.isFinite(value) && Math.abs(value) <= limit
  );
}

/** The request for a place, rounded first. */
export function planUrl(place: Place): string {
  const rounded = roundPlace(place);
  const query = new URLSearchParams({
    lat: rounded.latDeg.toFixed(PLACE_DECIMALS),
    lon: rounded.lonDeg.toFixed(PLACE_DECIMALS),
  });
  return `/api/v1/plan?${query.toString()}`;
}

function list(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new DecodeError(`${path}.${key}`, "an array");
  }
  return value;
}

function decodeYield(value: unknown, path: string): PlanYield {
  const fields = asObject(value, path);
  const status = asOneOf(
    fields,
    "status",
    ["computed", "not_measured"] as const,
    path,
  );
  return status === "computed"
    ? { probability: asNumber(fields, "probability", path) }
    : { notMeasured: asString(fields, "reason", path) };
}

function decodePass(value: unknown, path: string): PlanPass {
  const fields = asObject(value, path);
  return {
    satelliteId: asString(fields, "satellite_id", path),
    aos: asString(fields, "aos", path),
    los: asString(fields, "los", path),
    maxElevationDeg: asNumber(fields, "max_elevation_deg", path),
    maxElevationAt: asString(fields, "max_elevation_at", path),
    aosAzimuthDeg: asNumber(fields, "aos_azimuth_deg", path),
    losAzimuthDeg: asNumber(fields, "los_azimuth_deg", path),
    predictedYield: decodeYield(
      fields.predicted_yield,
      `${path}.predicted_yield`,
    ),
  };
}

function decodeSatellite(value: unknown, path: string): PlanSatellite {
  const fields = asObject(value, path);
  return {
    satelliteId: asString(fields, "satellite_id", path),
    name: asString(fields, "name", path),
    downlinks: list(fields, "downlinks", path).map((one, index) => {
      const where = `${path}.downlinks[${String(index)}]`;
      const downlink = asObject(one, where);
      return {
        centreFreqHz: asNumber(downlink, "centre_freq_hz", where),
        mode: asString(downlink, "mode", where),
      };
    }),
    elementSetEpoch: asString(fields, "element_set_epoch", path),
    elementSetAgeS: asNumber(fields, "element_set_age_s", path),
    elementSetSource: asString(fields, "element_set_source", path),
  };
}

function decodeOmission(value: unknown, path: string): PlanOmission {
  const fields = asObject(value, path);
  return {
    satelliteId: asString(fields, "satellite_id", path),
    name: asString(fields, "name", path),
    reason: asOneOf(
      fields,
      "reason",
      ["no_element_set", "propagation_failed", "over_limit"] as const,
      path,
    ),
  };
}

function decodeYieldModel(value: unknown, path: string): YieldModel {
  const fields = asObject(value, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  asOneOf(fields, "status", ["computed"] as const, path);
  return {
    configuration: asOneOf(fields, "configuration", ["A"] as const, path),
    population: asOneOf(
      fields,
      "population",
      ["own", "archive"] as const,
      path,
    ),
    modelSha256: asString(fields, "model_sha256", path),
    asOf: asString(fields, "as_of", path),
  };
}

export function decodePlan(body: unknown): Plan {
  const path = "plan";
  const fields = asObject(body, path);
  asOneOf(fields, "status", ["computed"] as const, path);
  const place = asObject(fields.place, `${path}.place`);
  return {
    at: asString(fields, "at", path),
    until: asString(fields, "until", path),
    place: {
      latDeg: asNumber(place, "lat_deg", `${path}.place`),
      lonDeg: asNumber(place, "lon_deg", `${path}.place`),
    },
    minElevationDeg: asNumber(fields, "min_elevation_deg", path),
    yieldModel: decodeYieldModel(fields.yield_model, `${path}.yield_model`),
    satellites: list(fields, "satellites", path).map((one, index) =>
      decodeSatellite(one, `${path}.satellites[${String(index)}]`),
    ),
    passes: list(fields, "passes", path).map((one, index) =>
      decodePass(one, `${path}.passes[${String(index)}]`),
    ),
    omitted: list(fields, "omitted", path).map((one, index) =>
      decodeOmission(one, `${path}.omitted[${String(index)}]`),
    ),
  };
}

export async function fetchPlan(
  fetcher: Fetcher,
  place: Place,
  signal: AbortSignal,
): Promise<Plan> {
  return decodePlan(
    await getJson(fetcher, planUrl(place), "the station planner", signal),
  );
}

/** "12.3° N, 77.6° E": a place as a reader writes it. */
export function formatPlace(place: Place): string {
  const lat = `${Math.abs(place.latDeg).toFixed(PLACE_DECIMALS)}° ${place.latDeg < 0 ? "S" : "N"}`;
  const lon = `${Math.abs(place.lonDeg).toFixed(PLACE_DECIMALS)}° ${place.lonDeg < 0 ? "W" : "E"}`;
  return `${lat}, ${lon}`;
}

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;

/** "NE": the eighth of the compass a bearing falls in. */
export function compassPoint(azimuthDeg: number): string {
  const index =
    Math.round((((azimuthDeg % 360) + 360) % 360) / 45) % POINTS.length;
  return POINTS[index] ?? "N";
}

/** The sum of every pass's probability: how many decodes to expect, or null
 * when the yields are not measured. A pass is one trial, so the sum is the
 * expected count, whatever the passes' order or overlap. */
export function expectedDecodes(passes: PlanPass[]): number | null {
  let total = 0;
  for (const one of passes) {
    if (!("probability" in one.predictedYield)) {
      return null;
    }
    total += one.predictedYield.probability;
  }
  return total;
}

/** Each searched satellite's passes, in the response's order. */
export function passesBySatellite(plan: Plan): Map<string, PlanPass[]> {
  const grouped = new Map<string, PlanPass[]>(
    plan.satellites.map((one) => [one.satelliteId, []]),
  );
  for (const one of plan.passes) {
    grouped.get(one.satelliteId)?.push(one);
  }
  return grouped;
}

const OMISSION_WORDS: Record<PlanOmission["reason"], string> = {
  no_element_set: "no orbit is held for it",
  propagation_failed: "its orbit could not be propagated",
  over_limit: "the catalogue is longer than the planner searches",
};

/** "Meteor-M2-4 was not searched: no orbit is held for it." */
export function describeOmission(one: PlanOmission): string {
  return `${one.name} was not searched: ${OMISSION_WORDS[one.reason]}.`;
}

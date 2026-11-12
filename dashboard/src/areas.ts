// Areas of interest: each published area's public-product series, its changes
// with their intervals, and every source with its terms (D-326, D-366).
//
// /api/v1/areas serves the summary a sealed regional report wrote, or says it
// is not published. Every value here is a public product's, never a station's,
// so nothing on these pages is simulated; the API says so with
// `simulated: false`. The page computes nothing from the values: a number no
// report sealed is one nobody can regenerate (rule 8).

import { getJson, type Fetcher } from "./api";
import { asNullableString, asNumber, asObject, asString, DecodeError, type Fields } from "./decode";

export interface AreaSource {
  sourceId: string;
  licence: string;
  termsUrl: string;
}

export interface Area {
  areaId: number;
  label: string;
  areaKm2: number;
  /** West, south, east, north, in degrees. */
  bbox: [number, number, number, number];
}

export interface SeriesPoint {
  areaId: number;
  quantity: string;
  unit: string;
  periodFrom: string;
  periodUntil: string;
  value: number | null;
  missingReason: string | null;
  count: number;
  sources: string[];
}

export interface AreaChange {
  areaId: number;
  quantity: string;
  verdict: string;
  reason: string;
  baseline: { from: string; until: string };
  current: { from: string; until: string };
  baselineMean: number | null;
  currentMean: number | null;
  change: number | null;
  low: number | null;
  high: number | null;
}

export interface AreasSummary {
  reportSha256: string;
  since: string;
  asOf: string;
  sources: AreaSource[];
  areas: Area[];
  series: SeriesPoint[];
  changes: AreaChange[];
}

/** The summary, or the reason this deployment publishes none. */
export type AreasRead = { summary: AreasSummary } | { notYetComputed: string };

function list(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new DecodeError(`${path}.${key}`, "an array");
  }
  return value;
}

function nullable(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

function strings(fields: Fields, key: string, path: string): string[] {
  return list(fields, key, path).map((one, index) => {
    if (typeof one !== "string") throw new DecodeError(`${path}.${key}[${String(index)}]`, "a string");
    return one;
  });
}

function decodeArea(value: unknown, path: string): Area {
  const fields = asObject(value, path);
  const box = list(fields, "bbox", path);
  if (box.length !== 4 || !box.every((one) => typeof one === "number")) {
    throw new DecodeError(`${path}.bbox`, "four numbers");
  }
  return {
    areaId: asNumber(fields, "area_id", path),
    label: asString(fields, "label", path),
    areaKm2: asNumber(fields, "area_km2", path),
    bbox: box as [number, number, number, number],
  };
}

function decodePoint(value: unknown, path: string): SeriesPoint {
  const fields = asObject(value, path);
  return {
    areaId: asNumber(fields, "area_id", path),
    quantity: asString(fields, "quantity", path),
    unit: asString(fields, "unit", path),
    periodFrom: asString(fields, "period_from", path),
    periodUntil: asString(fields, "period_until", path),
    value: nullable(fields, "value", path),
    missingReason: asNullableString(fields, "missing_reason", path),
    count: asNumber(fields, "count", path),
    sources: strings(fields, "sources", path),
  };
}

function period(value: unknown, path: string): { from: string; until: string } {
  const fields = asObject(value, path);
  return { from: asString(fields, "from", path), until: asString(fields, "until", path) };
}

function decodeChange(value: unknown, path: string): AreaChange {
  const fields = asObject(value, path);
  return {
    areaId: asNumber(fields, "area_id", path),
    quantity: asString(fields, "quantity", path),
    verdict: asString(fields, "verdict", path),
    reason: asString(fields, "reason", path),
    baseline: period(fields.baseline, `${path}.baseline`),
    current: period(fields.current, `${path}.current`),
    baselineMean: nullable(fields, "baseline_mean", path),
    currentMean: nullable(fields, "current_mean", path),
    change: nullable(fields, "change", path),
    low: nullable(fields, "low", path),
    high: nullable(fields, "high", path),
  };
}

export function decodeAreas(body: unknown): AreasRead {
  const fields = asObject(body, "areas");
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", "areas") };
  }
  const path = "areas.summary";
  const summary = asObject(fields.summary, path);
  const each = <T>(key: string, decode: (value: unknown, at: string) => T): T[] =>
    list(summary, key, path).map((one, index) => decode(one, `${path}.${key}[${String(index)}]`));
  return {
    summary: {
      reportSha256: asString(summary, "report_sha256", path),
      since: asString(summary, "since", path),
      asOf: asString(summary, "as_of", path),
      sources: each("sources", (one, at) => {
        const source = asObject(one, at);
        return {
          sourceId: asString(source, "source_id", at),
          licence: asString(source, "licence", at),
          termsUrl: asString(source, "terms_url", at),
        };
      }),
      areas: each("areas", decodeArea),
      series: each("series", decodePoint),
      changes: each("changes", decodeChange),
    },
  };
}

export async function fetchAreas(fetcher: Fetcher, signal: AbortSignal): Promise<AreasRead> {
  return decodeAreas(await getJson(fetcher, "/api/v1/areas", "the areas of interest", signal));
}

const QUANTITY_WORDS: Record<string, string> = {
  ndvi: "Vegetation (NDVI)",
  night_lights_radiance: "Night-time light",
  precipitation: "Rain",
  aerosol_optical_depth: "Aerosol optical depth",
  cloud_cover: "Cloud cover",
  fire_count: "Fire detections a day",
  fire_radiative_power_sum: "Fire radiative power",
};

/** "Cloud cover", or the quantity's own name for one not listed. */
export function quantityWords(quantity: string): string {
  return QUANTITY_WORDS[quantity] ?? quantity.replaceAll("_", " ");
}

/** "0.412", "12.5 mm": a value as published, with its unit unless it has none. */
export function formatValue(value: number, unit: string): string {
  const shown = Number(value.toPrecision(3)).toString();
  return unit === "1" || unit === "" ? shown : `${shown} ${unit}`;
}

const VERDICT_WORDS: Record<string, string> = {
  alert: "Changed beyond its threshold",
  within: "Within its threshold",
  insufficient: "Too few values to say",
};

/** The change's verdict in words (D-231). */
export function verdictWords(verdict: string): string {
  return VERDICT_WORDS[verdict] ?? verdict;
}

/** "−0.0100 [−0.0500, 0.0300]", or that nothing was measured. */
export function describeInterval(change: AreaChange): string {
  if (change.change === null) {
    return "no change measured";
  }
  const n = (value: number) => Number(value.toPrecision(3)).toString();
  const interval = change.low === null || change.high === null ? "" : ` [${n(change.low)}, ${n(change.high)}]`;
  return `${change.change > 0 ? "+" : ""}${n(change.change)}${interval}`;
}

/** One area's points, quantity by quantity, each in period order. */
export function seriesByQuantity(series: SeriesPoint[], areaId: number): Map<string, SeriesPoint[]> {
  const held = new Map<string, SeriesPoint[]>();
  for (const one of series.filter((point) => point.areaId === areaId)) {
    held.set(one.quantity, [...(held.get(one.quantity) ?? []), one]);
  }
  for (const points of held.values()) {
    points.sort((a, b) => a.periodFrom.localeCompare(b.periodFrom));
  }
  return held;
}

// The world outside the station: the latest Kp and cloud cover (D-331).
//
// /api/v1/conditions, each value credited to the source that published it
// (D-134) with when it was published. Measured by others, never by Meridian
// and never simulated. No Kp is said in words, never drawn as a zero (D-086).

import { getJson, type Fetcher } from "./api";
import { asNumber, asObject, asString, type Fields } from "./decode";

export interface Sample {
  value: number | null;
  missingReason: string | null;
  unit: string;
  observedFrom: string;
  observedTo: string;
  publishedAt: string;
  latDeg: number | null;
  lonDeg: number | null;
  source: { name: string; licence: string; termsUrl: string };
}

export interface Conditions {
  asOf: string;
  /** A sample, or the reason there is none. */
  kp: Sample | { notMeasured: string };
  cloudCover: Sample[];
}

function nullableNumber(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

function decodeSample(value: unknown, path: string): Sample {
  const fields = asObject(value, path);
  const source = asObject(fields.source, `${path}.source`);
  return {
    value: nullableNumber(fields, "value", path),
    missingReason: fields.missing_reason === null ? null : asString(fields, "missing_reason", path),
    unit: asString(fields, "unit", path),
    observedFrom: asString(fields, "observed_from", path),
    observedTo: asString(fields, "observed_to", path),
    publishedAt: asString(fields, "published_at", path),
    latDeg: nullableNumber(fields, "lat_deg", path),
    lonDeg: nullableNumber(fields, "lon_deg", path),
    source: {
      name: asString(source, "name", `${path}.source`),
      licence: asString(source, "licence", `${path}.source`),
      termsUrl: asString(source, "terms_url", `${path}.source`),
    },
  };
}

export function decodeConditions(body: unknown): Conditions {
  const path = "conditions";
  const fields = asObject(body, path);
  const kp = asObject(fields.kp, `${path}.kp`);
  const cloud = fields.cloud_cover;
  if (!Array.isArray(cloud)) {
    throw new Error(`${path}.cloud_cover: expected an array`);
  }
  return {
    asOf: asString(fields, "as_of", path),
    kp: kp.status === "not_measured" ? { notMeasured: asString(kp, "reason", `${path}.kp`) } : decodeSample(kp, `${path}.kp`),
    cloudCover: cloud.map((one, index) => decodeSample(one, `${path}.cloud_cover[${String(index)}]`)),
  };
}

export async function fetchConditions(fetcher: Fetcher, signal: AbortSignal): Promise<Conditions> {
  return decodeConditions(await getJson(fetcher, "/api/v1/conditions", "the conditions", signal));
}

/** "Kp 3.67", "64 % cloud", or the gap the source published. */
export function describeSample(sample: Sample, label: string): string {
  if (sample.value === null) {
    return `${label}: the source published no value (${sample.missingReason ?? "no reason given"})`;
  }
  return `${label} ${String(sample.value)}${sample.unit === "%" ? " %" : ""}`;
}

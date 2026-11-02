// A sealed model run's summary: how well the yield model is calibrated (D-332).
//
// /api/v1/insights/model, schema `model-summary-1`: per configuration, the
// Brier score against the base rate's, the skill, and ten calibration bins,
// with the label that says who measured the receptions behind them. A
// configuration the archive cannot support says why, with no number (D-086),
// and the whole read is `not_yet_computed` until a run is published.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString, type Fields } from "./decode";

export interface CalibrationBin {
  low: number;
  high: number;
  n: number;
  meanPredicted: number | null;
  observed: number | null;
}

export type Configuration =
  | {
      name: string;
      computable: true;
      nTest: number;
      brier: number;
      baseBrier: number;
      skill: number | null;
      bins: CalibrationBin[];
    }
  | { name: string; computable: false; reason: string };

export interface ModelInsights {
  runSha256: string;
  method: string;
  label: string;
  sourceName: string;
  simulated: boolean;
  completenessRatio: number | null;
  configurations: Configuration[];
}

export type InsightsRead = { computed: ModelInsights } | { notYetComputed: string };

function nullable(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

function asArray(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new Error(`${path}.${key}: expected an array`);
  }
  return value;
}

function decodeBin(value: unknown, path: string): CalibrationBin {
  const fields = asObject(value, path);
  return {
    low: asNumber(fields, "low", path),
    high: asNumber(fields, "high", path),
    n: asNumber(fields, "n", path),
    meanPredicted: nullable(fields, "mean_predicted", path),
    observed: nullable(fields, "observed", path),
  };
}

function decodeConfiguration(value: unknown, path: string): Configuration {
  const fields = asObject(value, path);
  const name = asString(fields, "name", path);
  if (!asBoolean(fields, "computable", path)) {
    return { name, computable: false, reason: asString(fields, "reason", path) };
  }
  return {
    name,
    computable: true,
    nTest: asNumber(fields, "n_test", path),
    brier: asNumber(fields, "brier", path),
    baseBrier: asNumber(fields, "base_brier", path),
    skill: nullable(fields, "skill", path),
    bins: asArray(fields, "bins", path).map((one, index) => decodeBin(one, `${path}.bins[${String(index)}]`)),
  };
}

export function decodeInsights(body: unknown): InsightsRead {
  const path = "insights";
  const fields = asObject(body, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  const source = asObject(fields.source, `${path}.source`);
  return {
    computed: {
      runSha256: asString(fields, "run_sha256", path),
      method: asString(fields, "method", path),
      label: asString(fields, "label", path),
      sourceName: asString(source, "name", `${path}.source`),
      simulated: asBoolean(fields, "simulated", path),
      completenessRatio: nullable(fields, "completeness_ratio", path),
      configurations: asArray(fields, "configurations", path).map((one, index) =>
        decodeConfiguration(one, `${path}.configurations[${String(index)}]`),
      ),
    },
  };
}

export async function fetchInsights(fetcher: Fetcher, signal: AbortSignal): Promise<InsightsRead> {
  return decodeInsights(await getJson(fetcher, "/api/v1/insights/model", "the model insight", signal));
}

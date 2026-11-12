// A sealed run's success criteria, as /api/v1/reports/criteria serves them (D-368).
//
// Schema `criteria-summary-1`: for SC-1 to SC-5 and SC-7 to SC-9, whether each
// was measured and on whose passes, its figures against their targets, and the
// section's own verdict. Every number is the sealed run's; the page judges
// nothing. A criterion not measured carries its reason and no number (D-086),
// and one measured on simulated fault runs says so (rule 5).

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNullableString, asNumber, asObject, asOneOf, asString, DecodeError, type Fields } from "./decode";

export interface CriterionFigure {
  name: string;
  value: number;
  low: number | null;
  high: number | null;
  target: number;
  direction: "at_least" | "at_most";
  n: number | null;
}

export type Criterion = {
  id: string;
  simulated: boolean;
  proposed: boolean;
} & (
  | {
      status: "measured";
      figures: CriterionFigure[];
      met: boolean;
      intervalClears: boolean | null;
      claimable: boolean;
      caveat: string | null;
    }
  | { status: "not_measured" | "not_run"; reason: string }
);

export interface Criteria {
  runSha256: string;
  method: string;
  since: string;
  asOf: string;
  criteria: Criterion[];
}

export type CriteriaRead = { computed: Criteria } | { notYetComputed: string };

function nullableNumber(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

function list(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new DecodeError(`${path}.${key}`, "an array");
  }
  return value;
}

function decodeFigure(value: unknown, path: string): CriterionFigure {
  const fields = asObject(value, path);
  return {
    name: asString(fields, "name", path),
    value: asNumber(fields, "value", path),
    low: nullableNumber(fields, "low", path),
    high: nullableNumber(fields, "high", path),
    target: asNumber(fields, "target", path),
    direction: asOneOf(fields, "direction", ["at_least", "at_most"] as const, path),
    n: nullableNumber(fields, "n", path),
  };
}

function decodeCriterion(value: unknown, path: string): Criterion {
  const fields = asObject(value, path);
  const base = {
    id: asString(fields, "id", path),
    simulated: asBoolean(fields, "simulated", path),
    proposed: asBoolean(fields, "proposed", path),
  };
  const status = asOneOf(fields, "status", ["measured", "not_measured", "not_run"] as const, path);
  if (status !== "measured") {
    return { ...base, status, reason: asString(fields, "reason", path) };
  }
  return {
    ...base,
    status,
    figures: list(fields, "figures", path).map((one, index) => decodeFigure(one, `${path}.figures[${String(index)}]`)),
    met: asBoolean(fields, "met", path),
    intervalClears: fields.interval_clears === null ? null : asBoolean(fields, "interval_clears", path),
    claimable: asBoolean(fields, "claimable", path),
    caveat: asNullableString(fields, "caveat", path),
  };
}

export function decodeCriteria(body: unknown): CriteriaRead {
  const path = "criteria";
  const fields = asObject(body, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  return {
    computed: {
      runSha256: asString(fields, "run_sha256", path),
      method: asString(fields, "method", path),
      since: asString(fields, "since", path),
      asOf: asString(fields, "as_of", path),
      criteria: list(fields, "criteria", path).map((one, index) =>
        decodeCriterion(one, `${path}.criteria[${String(index)}]`),
      ),
    },
  };
}

export async function fetchCriteria(fetcher: Fetcher, signal: AbortSignal): Promise<CriteriaRead> {
  return decodeCriteria(await getJson(fetcher, "/api/v1/reports/criteria", "the success criteria", signal));
}

/** Each criterion's claim, as `EVALUATION.md` §1 states it. */
export const CLAIMS: Record<string, string> = {
  "SC-1": "Yield-predicting scheduling beats existing practice",
  "SC-2": "Predictions are calibrated",
  "SC-3": "Orbital timing uncertainty is correctly estimated",
  "SC-4": "Our station is reliable",
  "SC-5": "Failures are detected quickly",
  "SC-7": "The reception verdict is calibrated",
  "SC-8": "Loss diagnosis names the injected cause",
  "SC-9": "The health watch warns before reception fails",
};

const FIGURE_WORDS: Record<string, string> = {
  relative_gain: "Gain in decoded frames per station-hour over existing practice",
  skill: "Brier skill against the base rate",
  coverage: "Passes inside their stated 1σ",
  capture_rate: "Capture rate",
  slowest_detection_s: "Slowest detection",
  wrong_fraction: "Diagnoses naming a wrong cause",
  warned_first: "Degradations warned of before the first failed reception",
  false_alarms_per_station_30_days: "False alarms per station per 30 days",
};

/** A figure's name in words: "Recall of obstruction" for a per-cause recall. */
export function figureWords(name: string): string {
  if (name.startsWith("recall_")) {
    return `Recall of ${name.slice("recall_".length).replaceAll("_", " ")}`;
  }
  return FIGURE_WORDS[name] ?? name.replaceAll("_", " ");
}

const SHARE = new Set([
  "relative_gain",
  "skill",
  "coverage",
  "capture_rate",
  "wrong_fraction",
  "warned_first",
]);

/** "24%", "68 s", "0.4": a value in the unit its figure is in. */
export function formatFigureValue(name: string, value: number): string {
  if (name.endsWith("_s")) {
    return `${Number(value.toPrecision(3)).toString()} s`;
  }
  if (SHARE.has(name) || name.startsWith("recall_")) {
    return `${String(Math.round(value * 100))}%`;
  }
  return Number(value.toPrecision(3)).toString();
}

/** What a figure's `n` counts. */
const COUNTED: Record<string, string> = {
  relative_gain: "station-days",
  skill: "held-out cases",
  coverage: "passes",
  capture_rate: "passes",
  slowest_detection_s: "timed faults",
  wrong_fraction: "diagnoses",
  warned_first: "degradations",
};

/** "24% (11% to 37%); target at least 20%, over 58 station-days". */
export function describeFigure(figure: CriterionFigure): string {
  const shown = (value: number) => formatFigureValue(figure.name, value);
  const interval = figure.low === null || figure.high === null ? "" : ` (${shown(figure.low)} to ${shown(figure.high)})`;
  const side = figure.direction === "at_least" ? "at least" : "at most";
  const counted = figure.name.startsWith("recall_") ? "cases" : (COUNTED[figure.name] ?? "");
  const over = figure.n === null ? "" : `, over ${String(figure.n)} ${counted}`.trimEnd();
  return `${shown(figure.value)}${interval}; target ${side} ${shown(figure.target)}${over}`;
}

/** The section's verdict in words: met, met but not to be claimed, or not met. */
export function verdictOf(criterion: Criterion): string {
  if (criterion.status !== "measured") {
    return criterion.status === "not_run" ? "Not judged by this run" : "Not measured";
  }
  if (!criterion.met) {
    return "Target not met";
  }
  return criterion.claimable ? "Target met" : "Target met, not yet claimed";
}

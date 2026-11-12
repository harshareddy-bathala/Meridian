// Every settled pass across the network, and what became of it (D-365).
//
// /api/v1/results lists each classified pass, newest first, with its window
// widened to whole minutes (D-093). Whether a class counts as captured, lost or
// the satellite's own is the platform's reading (``effect``), never restated
// here, so the page cannot disagree with the reliability report.

import { decodeItems, getJson, type Fetcher } from "./api";
import { asBoolean, asNullableString, asNumber, asObject, asOneOf, asString, type Fields } from "./decode";

export const EFFECTS = ["captured", "lost", "satellite"] as const;

export interface PassResult {
  classificationId: number;
  assignmentId: string;
  stationId: string;
  satelliteId: string;
  windowStart: string;
  windowEnd: string;
  classification: string;
  effect: (typeof EFFECTS)[number];
  listeningConfirmed: boolean;
  outcome: string | null;
  diagnosisId: number | null;
  cause: string | null;
  simulated: boolean;
}

/** How many results one page holds. */
export const RESULTS_LIMIT = 50;

function nullableNumber(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

export function decodeResult(value: unknown, path: string): PassResult {
  const fields = asObject(value, path);
  return {
    classificationId: asNumber(fields, "classification_id", path),
    assignmentId: asString(fields, "assignment_id", path),
    stationId: asString(fields, "station_id", path),
    satelliteId: asString(fields, "satellite_id", path),
    windowStart: asString(fields, "window_start", path),
    windowEnd: asString(fields, "window_end", path),
    classification: asString(fields, "classification", path),
    effect: asOneOf(fields, "effect", EFFECTS, path),
    listeningConfirmed: asBoolean(fields, "listening_confirmed", path),
    outcome: asNullableString(fields, "outcome", path),
    diagnosisId: nullableNumber(fields, "diagnosis_id", path),
    cause: asNullableString(fields, "cause", path),
    simulated: asBoolean(fields, "simulated", path),
  };
}

export interface ResultPage {
  results: PassResult[];
  nextCursor: string | null;
}

/** One page of results: the newest when ``cursor`` is null. */
export async function fetchResults(
  fetcher: Fetcher,
  cursor: string | null,
  signal: AbortSignal,
): Promise<ResultPage> {
  const query = new URLSearchParams({ limit: String(RESULTS_LIMIT) });
  if (cursor !== null) {
    query.set("cursor", cursor);
  }
  const body = await getJson(fetcher, `/api/v1/results?${query.toString()}`, "the results", signal);
  const page = decodeItems(body, decodeResult);
  return { results: page.items, nextCursor: page.nextCursor };
}

const CLASS_WORDS: Record<string, string> = {
  successful_reception: "Decoded",
  signal_no_decode: "Heard, not decoded",
  confirmed_miss: "Missed while listening",
  satellite_silent: "The satellite was silent",
  satellite_state_indeterminate: "The satellite's state is undetermined",
  station_unavailable: "Station unavailable",
  station_not_confirmed_listening: "Not confirmed listening",
  assignment_declined: "Declined",
};

/** "Decoded", "Station unavailable": the class in words. */
export function classWords(classification: string): string {
  return CLASS_WORDS[classification] ?? classification;
}

const EFFECT_WORDS: Record<PassResult["effect"], string> = {
  captured: "captured",
  lost: "lost: spends the loss budget",
  satellite: "the satellite's own: counts neither way",
};

/** What the pass did to SC-4, in words. */
export function effectWords(effect: PassResult["effect"]): string {
  return EFFECT_WORDS[effect];
}

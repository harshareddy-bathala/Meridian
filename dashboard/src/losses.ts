// Every lost pass the platform diagnosed, and why: /api/v1/losses (D-331).
//
// A loss names its cause or *undetermined*, and every cause that was tested,
// fired or not, with what the test found (D-273). *Undetermined* is an answer:
// no test fired, or two fired too close to choose, and the view says so in
// those words rather than guessing.

import { decodeItems, getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asOneOf, asString, type Fields } from "./decode";

export const CAUSES = [
  "satellite_silent",
  "station_not_listening",
  "obstruction",
  "interference",
  "timing_fault",
  "undetermined",
] as const;
export type Cause = (typeof CAUSES)[number];

export type Finding = number | string | boolean | null;

export interface CauseTest {
  cause: string;
  fired: boolean;
  support: number;
  findings: Record<string, Finding>;
}

export interface Loss {
  diagnosisId: number;
  assignmentId: string;
  stationId: string;
  satelliteId: string;
  windowStart: string;
  windowEnd: string;
  classification: string;
  cause: Cause;
  tested: CauseTest[];
  method: string;
  simulated: boolean;
}

function asArray(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new Error(`${path}.${key}: expected an array`);
  }
  return value;
}

function decodeFindings(value: unknown, path: string): Record<string, Finding> {
  const fields = asObject(value, path);
  const found: Record<string, Finding> = {};
  for (const [key, held] of Object.entries(fields)) {
    if (held !== null && !["number", "string", "boolean"].includes(typeof held)) {
      throw new Error(`${path}.${key}: expected a number, a string, a boolean or null`);
    }
    found[key] = held as Finding;
  }
  return found;
}

function decodeTest(value: unknown, path: string): CauseTest {
  const fields = asObject(value, path);
  return {
    cause: asString(fields, "cause", path),
    fired: asBoolean(fields, "fired", path),
    support: asNumber(fields, "support", path),
    findings: decodeFindings(fields.findings, `${path}.findings`),
  };
}

export function decodeLoss(value: unknown, path: string): Loss {
  const fields = asObject(value, path);
  return {
    diagnosisId: asNumber(fields, "diagnosis_id", path),
    assignmentId: asString(fields, "assignment_id", path),
    stationId: asString(fields, "station_id", path),
    satelliteId: asString(fields, "satellite_id", path),
    windowStart: asString(fields, "window_start", path),
    windowEnd: asString(fields, "window_end", path),
    classification: asString(fields, "classification", path),
    cause: asOneOf(fields, "cause", CAUSES, path),
    tested: asArray(fields, "tested", path).map((one, index) =>
      decodeTest(one, `${path}.tested[${String(index)}]`),
    ),
    method: asString(fields, "method", path),
    simulated: asBoolean(fields, "simulated", path),
  };
}

export const LOSSES_LIMIT = 50;

/** The most recently diagnosed losses, the network's or one station's. */
export async function fetchLosses(
  fetcher: Fetcher,
  stationId: string | null,
  signal: AbortSignal,
): Promise<Loss[]> {
  const query = new URLSearchParams({ limit: String(LOSSES_LIMIT) });
  if (stationId !== null) {
    query.set("station_id", stationId);
  }
  const body = await getJson(fetcher, `/api/v1/losses?${query.toString()}`, "the loss list", signal);
  return decodeItems(body, decodeLoss).items;
}

const CAUSE_WORDS: Record<string, string> = {
  satellite_silent: "the satellite was silent",
  station_not_listening: "the station was not listening",
  obstruction: "something blocks that part of the sky",
  interference: "the noise floor was raised",
  timing_fault: "the station's clock was wrong",
  undetermined: "undetermined",
};

/** The cause in words a station owner reads: "the satellite was silent". */
export function causeWords(cause: string): string {
  return CAUSE_WORDS[cause] ?? cause.replaceAll("_", " ");
}

/** Why a loss is undetermined, from what was tested; null when a cause was named. */
export function whyUndetermined(loss: Loss): string | null {
  if (loss.cause !== "undetermined") {
    return null;
  }
  const fired = loss.tested.filter((one) => one.fired);
  if (fired.length === 0) {
    return `No test found its evidence: ${String(loss.tested.length)} causes tested, none fired.`;
  }
  const names = fired.map((one) => causeWords(one.cause)).join("; ");
  return `More than one cause fired, too close to choose between: ${names}.`;
}

/** One finding as text: "lost_inside 9", "share 0.41". */
export function describeFinding(key: string, value: Finding): string {
  const name = key.replaceAll("_", " ");
  if (value === null) {
    return `${name}: none`;
  }
  return `${name} ${String(value)}`;
}

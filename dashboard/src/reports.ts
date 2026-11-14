// The network's weekly report: one settled week in plain words (D-367).
//
// /api/v1/reports/weekly serves the text template `network-weekly-1` wrote from
// the stored classifications, with the hashes that say whether two reads found
// the same facts. The page shows the text as it is, line by line, and builds
// nothing from it but the blocks its blank lines separate: a block whose
// heading says SIMULATED is badged as well as said (rule 5).

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNullableString, asObject, asString } from "./decode";

export interface NetworkWeekly {
  template: string;
  periodStart: string;
  periodEnd: string;
  /** The Monday before, or null where the network had no station yet. */
  previousWeek: string | null;
  asOf: string;
  includesSimulated: boolean;
  subject: string;
  body: string;
  inputsSha256: string;
  contentSha256: string;
}

export function decodeWeekly(body: unknown): NetworkWeekly {
  const path = "weekly";
  const fields = asObject(body, path);
  return {
    template: asString(fields, "template", path),
    periodStart: asString(fields, "period_start", path),
    periodEnd: asString(fields, "period_end", path),
    previousWeek: asNullableString(fields, "previous_week", path),
    asOf: asString(fields, "as_of", path),
    includesSimulated: asBoolean(fields, "includes_simulated", path),
    subject: asString(fields, "subject", path),
    body: asString(fields, "body", path),
    inputsSha256: asString(fields, "inputs_sha256", path),
    contentSha256: asString(fields, "content_sha256", path),
  };
}

/** The latest settled week, the one the front keeps a copy of (D-283). */
export async function fetchWeekly(fetcher: Fetcher, signal: AbortSignal): Promise<NetworkWeekly> {
  return decodeWeekly(await getJson(fetcher, "/api/v1/reports/weekly", "the network's weekly report", signal));
}

/** An earlier week, by its Monday. Not warmed: in an outage it says so. */
export async function fetchWeekOf(fetcher: Fetcher, monday: string, signal: AbortSignal): Promise<NetworkWeekly> {
  const url = `/api/v1/reports/weekly?week=${encodeURIComponent(monday)}`;
  return decodeWeekly(await getJson(fetcher, url, "the network's weekly report", signal));
}

export interface Block {
  lines: string[];
  /** The first line is a section's heading: its first word is in capitals. */
  headed: boolean;
  simulated: boolean;
}

/** The text's blocks, as its blank lines separate them. */
export function blocksOf(body: string): Block[] {
  return body
    .split("\n\n")
    .map((block) => block.split("\n").filter((line) => line !== ""))
    .filter((lines) => lines.length > 0)
    .map((lines) => {
      const first = lines[0] ?? "";
      return { lines, headed: /^[A-Z]{2,}\b/.test(first), simulated: first.startsWith("SIMULATED ") };
    });
}

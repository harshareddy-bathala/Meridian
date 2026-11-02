// The newest scheduling round's own record (D-331).
//
// /api/v1/schedule-runs/latest: the configuration it scheduled with, the
// solver's status and objective, and its decisions by population. Its gain over
// a baseline is `not_measured` live, with the reason: the replay measures it on
// a snapshot (D-172). Before any round has decided anything there is no record,
// and the read says `not_found`, which is shown as that and not as an error.

import { getJson, type Fetcher } from "./api";
import { asNumber, asObject, asOneOf, asString } from "./decode";
import { RUN_STATUSES } from "./schedule";

export interface ScheduleRun {
  runId: string;
  decidedAt: string;
  configuration: string;
  solverStatus: (typeof RUN_STATUSES)[number];
  objective: number;
  runtimeS: number;
  candidates: number;
  scheduled: number;
  skipped: number;
  simulatedDecisions: number;
  measuredDecisions: number;
  /** Why the gain over a baseline is not given live. */
  baselineGain: string;
}

export function decodeScheduleRun(body: unknown): ScheduleRun {
  const path = "schedule_run";
  const fields = asObject(body, path);
  const gain = asObject(fields.baseline_gain, `${path}.baseline_gain`);
  return {
    runId: asString(fields, "run_id", path),
    decidedAt: asString(fields, "decided_at", path),
    configuration: asString(fields, "configuration", path),
    solverStatus: asOneOf(fields, "solver_status", RUN_STATUSES, path),
    objective: asNumber(fields, "objective", path),
    runtimeS: asNumber(fields, "runtime_s", path),
    candidates: asNumber(fields, "candidates", path),
    scheduled: asNumber(fields, "scheduled", path),
    skipped: asNumber(fields, "skipped", path),
    simulatedDecisions: asNumber(fields, "simulated_decisions", path),
    measuredDecisions: asNumber(fields, "measured_decisions", path),
    baselineGain: asString(gain, "reason", `${path}.baseline_gain`),
  };
}

/** The newest round, or null before any round has decided anything. */
export async function fetchLatestRun(fetcher: Fetcher, signal: AbortSignal): Promise<ScheduleRun | null> {
  const url = "/api/v1/schedule-runs/latest";
  const response = await fetcher(url, { signal });
  if (response.status === 404) {
    return null;
  }
  return decodeScheduleRun(await getJson(() => Promise.resolve(response), url, "the latest round", signal));
}

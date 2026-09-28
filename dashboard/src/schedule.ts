// What a station is doing now and what it has been given to do next.
//
// Two reads: the latest heartbeat, for the listening state, and the upcoming
// assignments with the scheduler's reasons. Windows arrive already widened to
// whole minutes (D-093); nothing here can recover more and nothing should imply it.

import { decodeItems, getJson, type Fetcher } from "./api";
import {
  asBoolean,
  asNullableString,
  asNumber,
  asObject,
  asOneOf,
  asString,
} from "./decode";

export const DECISIONS = ["scheduled", "skipped"] as const;
export type Decision = (typeof DECISIONS)[number];

export const YIELD_SOURCES = ["model", "elevation_proxy"] as const;
export const RUN_STATUSES = ["optimal", "time_limit", "fallback"] as const;

/** A pass weighed against another: what it was, and what it was worth. */
export interface Weighed {
  passId: number;
  decision: string;
  /** Null for a commitment an earlier run made, which this run did not value. */
  value: number | null;
}

/** Why a decision went the way it did, as the scheduler stored it (D-170). */
export interface Explanation {
  value: number;
  yield: number;
  yieldSource: (typeof YIELD_SOURCES)[number];
  frames: number;
  framesTerm: string;
  priority: number;
  priorityWeighted: boolean;
  /** For a skip, what took its slot; for a selection, the best it displaced. */
  alternative: Weighed | null;
  runStatus: (typeof RUN_STATUSES)[number];
}

export interface Assignment {
  assignmentId: string;
  stationId: string;
  satelliteId: string;
  startAt: string;
  endAt: string;
  decision: Decision;
  reason: string;
  conflictsWith: string | null;
  /** Null for a skip: a station is never given one, so it has no state (D-165). */
  state: string | null;
  simulated: boolean;
  /** Null for a decision made before Stage 18, which no recorded run made. */
  explanation: Explanation | null;
}

export interface Listening {
  assignmentId: string;
  satelliteId: string;
  centreFreqHz: number;
  mode: string;
}

export interface LatestHeartbeat {
  receivedAt: string;
  state: string;
  /** Null means the station said it was not listening, not that nothing is known. */
  listening: Listening | null;
  simulated: boolean;
}

export function decodeAssignment(value: unknown, path: string): Assignment {
  const fields = asObject(value, path);
  return {
    assignmentId: asString(fields, "assignment_id", path),
    stationId: asString(fields, "station_id", path),
    satelliteId: asString(fields, "satellite_id", path),
    startAt: asString(fields, "start_at", path),
    endAt: asString(fields, "end_at", path),
    decision: asOneOf(fields, "decision", DECISIONS, path),
    reason: asString(fields, "reason", path),
    conflictsWith: asNullableString(fields, "conflicts_with_assignment_id", path),
    state: asNullableString(fields, "state", path),
    simulated: asBoolean(fields, "simulated", path),
    explanation: decodeExplanation(fields.explanation, `${path}.explanation`),
  };
}

function decodeWeighed(value: unknown, path: string): Weighed | null {
  if (value === null) {
    return null;
  }
  const fields = asObject(value, path);
  return {
    passId: asNumber(fields, "pass_id", path),
    decision: asString(fields, "decision", path),
    value: fields.value === null ? null : asNumber(fields, "value", path),
  };
}

export function decodeExplanation(value: unknown, path: string): Explanation | null {
  if (value === null) {
    return null;
  }
  const fields = asObject(value, path);
  const terms = asObject(fields.terms, `${path}.terms`);
  const run = asObject(fields.run, `${path}.run`);
  return {
    value: asNumber(terms, "value", `${path}.terms`),
    yield: asNumber(terms, "yield", `${path}.terms`),
    yieldSource: asOneOf(terms, "yield_source", YIELD_SOURCES, `${path}.terms`),
    frames: asNumber(terms, "frames", `${path}.terms`),
    framesTerm: asString(terms, "frames_term", `${path}.terms`),
    priority: asNumber(terms, "priority", `${path}.terms`),
    priorityWeighted: asBoolean(terms, "priority_weighted", `${path}.terms`),
    alternative: decodeWeighed(fields.alternative, `${path}.alternative`),
    runStatus: asOneOf(run, "status", RUN_STATUSES, `${path}.run`),
  };
}

/** The value as the product it is: "0.62 × 720 s × priority 1.5 = 669.6". */
export function describeValue(explanation: Explanation): string {
  const source = explanation.yieldSource === "model" ? "model" : "elevation proxy";
  const factors = [`yield ${explanation.yield.toFixed(2)} (${source})`];
  if (explanation.framesTerm === "duration") {
    factors.push(`${explanation.frames.toFixed(0)} s`);
  }
  if (explanation.priorityWeighted) {
    factors.push(`priority ${String(explanation.priority)}`);
  }
  return `${factors.join(" × ")} = ${explanation.value.toFixed(1)}`;
}

function decodeListening(value: unknown, path: string): Listening | null {
  if (value === null) {
    return null;
  }
  const fields = asObject(value, path);
  return {
    assignmentId: asString(fields, "assignment_id", path),
    satelliteId: asString(fields, "satellite_id", path),
    centreFreqHz: asNumber(fields, "centre_freq_hz", path),
    mode: asString(fields, "mode", path),
  };
}

export function decodeHeartbeat(value: unknown, path: string): LatestHeartbeat {
  const fields = asObject(value, path);
  return {
    receivedAt: asString(fields, "received_at", path),
    state: asString(fields, "state", path),
    listening: decodeListening(fields.listening, `${path}.listening`),
    simulated: asBoolean(fields, "simulated", path),
  };
}

export const UPCOMING_LIMIT = 10;

/** The next assignments, scheduled and skipped, for one station or the network. */
export async function fetchUpcomingAssignments(
  fetcher: Fetcher,
  stationId: string | null,
  signal: AbortSignal,
): Promise<Assignment[]> {
  const query = new URLSearchParams({ limit: String(UPCOMING_LIMIT) });
  if (stationId !== null) {
    query.set("station_id", stationId);
  }
  const url = `/api/v1/assignments?${query.toString()}`;
  const body = await getJson(fetcher, url, "the assignment list", signal);
  return decodeItems(body, decodeAssignment).items;
}

/** The station's most recent heartbeat, or null if it has never sent one. */
export async function fetchLatestHeartbeat(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<LatestHeartbeat | null> {
  const url = `/api/v1/stations/${encodeURIComponent(stationId)}/heartbeats?limit=1`;
  const body = await getJson(fetcher, url, "the heartbeat list", signal);
  return decodeItems(body, decodeHeartbeat).items[0] ?? null;
}

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

export interface Assignment {
  assignmentId: string;
  stationId: string;
  satelliteId: string;
  startAt: string;
  endAt: string;
  decision: Decision;
  reason: string;
  conflictsWith: string | null;
  state: string;
  simulated: boolean;
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
    state: asString(fields, "state", path),
    simulated: asBoolean(fields, "simulated", path),
  };
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

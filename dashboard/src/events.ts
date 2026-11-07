// What just happened across the network, newest first (D-363).
//
// /api/v1/events reads each event back from the record that says so: a
// station's registration, a decode, a diagnosed loss, a receive-chain warning
// raised or cleared. Each carries ``simulated`` from its record (rule 5).

import { decodeItems, getJson, type Fetcher } from "./api";
import { asBoolean, asNullableString, asObject, asOneOf, asString } from "./decode";

export const EVENT_KINDS = [
  "loss_diagnosed",
  "pass_decoded",
  "station_joined",
  "warning_cleared",
  "warning_raised",
] as const;

export interface NetworkEvent {
  at: string;
  kind: (typeof EVENT_KINDS)[number];
  ref: string;
  stationId: string;
  simulated: boolean;
  satelliteId: string | null;
  assignmentId: string | null;
  cause: string | null;
}

/** How many the live page lists. */
export const FEED_LIMIT = 20;

export function decodeEvent(value: unknown, path: string): NetworkEvent {
  const fields = asObject(value, path);
  return {
    at: asString(fields, "at", path),
    kind: asOneOf(fields, "kind", EVENT_KINDS, path),
    ref: asString(fields, "ref", path),
    stationId: asString(fields, "station_id", path),
    simulated: asBoolean(fields, "simulated", path),
    satelliteId: asNullableString(fields, "satellite_id", path),
    assignmentId: asNullableString(fields, "assignment_id", path),
    cause: asNullableString(fields, "cause", path),
  };
}

export async function fetchEvents(fetcher: Fetcher, signal: AbortSignal): Promise<NetworkEvent[]> {
  const url = `/api/v1/events?limit=${String(FEED_LIMIT)}`;
  return decodeItems(await getJson(fetcher, url, "the events feed", signal), decodeEvent).items;
}

const CAUSE_WORDS: Record<string, string> = {
  satellite_silent: "the satellite was silent",
  station_not_listening: "the station was not listening",
  obstruction: "something blocked the sky",
  interference: "interference",
  timing_fault: "a timing fault",
  undetermined: "cause undetermined",
};

function satellite(event: NetworkEvent): string {
  return event.satelliteId ?? "a satellite";
}

function cause(event: NetworkEvent): string {
  return CAUSE_WORDS[event.cause ?? "undetermined"] ?? event.cause ?? "cause undetermined";
}

const WORDS: Record<NetworkEvent["kind"], (event: NetworkEvent) => string> = {
  station_joined: () => "joined the network",
  pass_decoded: (event) => `decoded a pass of ${satellite(event)}`,
  loss_diagnosed: (event) => `lost a pass of ${satellite(event)}: ${cause(event)}`,
  warning_raised: () => "receive chain fell short of its baseline",
  warning_cleared: () => "receive chain recovered",
};

/** What happened, in words, with the station named by the caller. */
export function describeEvent(event: NetworkEvent): string {
  return WORDS[event.kind](event);
}

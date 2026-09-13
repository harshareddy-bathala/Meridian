// The station directory, as GET /api/v1/stations publishes it.
//
// Coordinates here are already coarsened to what each operator permitted
// (D-082); nothing in the dashboard can recover more, and nothing should imply
// it. `simulated` is required: a body without it is refused rather than drawn,
// because a virtual station shown as a real one is the failure CLAUDE.md rule 5
// exists to prevent.

import {
  asBoolean,
  asNullableString,
  asNumber,
  asObject,
  asOneOf,
  asString,
  DecodeError,
} from "./decode";

export const LIVENESS = ["online", "stale", "offline", "never_seen"] as const;
export type Liveness = (typeof LIVENESS)[number];

export interface Station {
  stationId: string;
  name: string;
  operator: string;
  latDeg: number;
  lonDeg: number;
  altM: number;
  locationPrecisionDecimals: number;
  simulated: boolean;
  liveness: Liveness;
  lastHeartbeatAt: string | null;
  registeredAt: string;
}

export interface StationPage {
  stations: Station[];
  nextCursor: string | null;
}

/** The API's own page cap (D-085). Asking for more is an `invalid_query`. */
export const PAGE_LIMIT = 200;

/** Stops a cursor that never ends from looping forever: 10,000 stations. */
export const MAX_PAGES = 50;

export function decodeStation(value: unknown, path: string): Station {
  const fields = asObject(value, path);
  const location = asObject(fields.location, `${path}.location`);
  return {
    stationId: asString(fields, "station_id", path),
    name: asString(fields, "name", path),
    operator: asString(fields, "operator", path),
    latDeg: asNumber(location, "lat_deg", `${path}.location`),
    lonDeg: asNumber(location, "lon_deg", `${path}.location`),
    altM: asNumber(location, "alt_m", `${path}.location`),
    locationPrecisionDecimals: asNumber(fields, "location_precision_decimals", path),
    simulated: asBoolean(fields, "simulated", path),
    liveness: asOneOf(fields, "liveness", LIVENESS, path),
    lastHeartbeatAt: asNullableString(fields, "last_heartbeat_at", path),
    registeredAt: asString(fields, "registered_at", path),
  };
}

export function decodeStationPage(body: unknown): StationPage {
  const fields = asObject(body, "page");
  const items = fields.items;
  if (!Array.isArray(items)) {
    throw new DecodeError("page.items", "an array");
  }
  const cursor = fields.next_cursor;
  return {
    stations: items.map((item, index) => decodeStation(item, `page.items[${String(index)}]`)),
    nextCursor: cursor === undefined ? null : asNullableString(fields, "next_cursor", "page"),
  };
}

export type Fetcher = (url: string, init: { signal: AbortSignal }) => Promise<Response>;

async function fetchPage(
  fetcher: Fetcher,
  cursor: string | null,
  signal: AbortSignal,
): Promise<StationPage> {
  const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
  if (cursor !== null) {
    query.set("cursor", cursor);
  }
  const response = await fetcher(`/api/v1/stations?${query.toString()}`, { signal });
  const body: unknown = await response.json();
  if (!response.ok) {
    // D-084's envelope; fall back to the status if even that is missing.
    const message = typeof body === "object" && body !== null && "message" in body
      ? String(body.message)
      : `HTTP ${String(response.status)}`;
    throw new Error(`the station directory refused the request: ${message}`);
  }
  return decodeStationPage(body);
}

/** Every station, walking the keyset cursor until the API says there is no more. */
export async function fetchAllStations(fetcher: Fetcher, signal: AbortSignal): Promise<Station[]> {
  const stations: Station[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result = await fetchPage(fetcher, cursor, signal);
    stations.push(...result.stations);
    cursor = result.nextCursor;
    if (cursor === null) {
      return stations;
    }
  }
  throw new Error(`the station directory did not end after ${String(MAX_PAGES)} pages`);
}

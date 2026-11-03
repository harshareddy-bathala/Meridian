// One station's next hours, as the scheduler decided them (D-331).
//
// /api/v1/schedule: each pass scheduled or skipped, with its explanation
// (D-170), its peak elevation to the degree (D-093), and the epoch of the
// element set that predicted it. Element-set age is a first-class figure: an
// orbit weeks old puts the pass minutes from where it is drawn.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString, type Fields } from "./decode";
import { type Assignment, decodeAssignment } from "./schedule";

export interface ScheduleEntry {
  assignment: Assignment;
  maxElevationDeg: number;
  elementSetEpoch: string;
}

export interface StationSchedule {
  stationId: string;
  simulated: boolean;
  start: string;
  end: string;
  entries: ScheduleEntry[];
}

function asArray(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new Error(`${path}.${key}: expected an array`);
  }
  return value;
}

function decodeEntry(value: unknown, path: string): ScheduleEntry {
  const fields = asObject(value, path);
  return {
    assignment: decodeAssignment(fields.assignment, `${path}.assignment`),
    maxElevationDeg: asNumber(fields, "max_elevation_deg", path),
    elementSetEpoch: asString(fields, "element_set_epoch", path),
  };
}

export function decodeStationSchedule(body: unknown): StationSchedule {
  const path = "schedule";
  const fields = asObject(body, path);
  return {
    stationId: asString(fields, "station_id", path),
    simulated: asBoolean(fields, "simulated", path),
    start: asString(fields, "start", path),
    end: asString(fields, "end", path),
    entries: asArray(fields, "entries", path).map((one, index) =>
      decodeEntry(one, `${path}.entries[${String(index)}]`),
    ),
  };
}

export const SCHEDULE_HOURS = 6;

export async function fetchStationSchedule(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<StationSchedule> {
  const query = new URLSearchParams({ station_id: stationId, hours: String(SCHEDULE_HOURS) });
  const body = await getJson(fetcher, `/api/v1/schedule?${query.toString()}`, "the schedule", signal);
  return decodeStationSchedule(body);
}

const DAY_MS = 86_400_000;

/** How old the orbit behind a pass was when the pass rises, in days. */
export function elementSetAgeDays(entry: ScheduleEntry): number {
  return (Date.parse(entry.assignment.startAt) - Date.parse(entry.elementSetEpoch)) / DAY_MS;
}

/** "orbit 2.4 days old", with the warning a stale one deserves. */
export function describeElementSetAge(entry: ScheduleEntry): string {
  const days = elementSetAgeDays(entry);
  const age = `orbit ${days.toFixed(1)} days old`;
  return days > STALE_ORBIT_DAYS ? `${age}: predicted times may be off by minutes` : age;
}

/** Older than this, a pass's predicted times are not to be trusted to the minute. */
export const STALE_ORBIT_DAYS = 3;

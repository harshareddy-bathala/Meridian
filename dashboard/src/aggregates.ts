// Today's passes and a week by day, for measured and simulated stations apart
// (D-362).
//
// /api/v1/aggregates counts both by the reliability report's own rules. The
// dashboard only groups today's classes into what a reader asks: decoded,
// lost, or the satellite's own doing. A pass the satellite decided is neither
// captured nor lost (D-185), so it is never counted as lost here.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString, DecodeError, type Fields } from "./decode";
import { decodeProportion, type Proportion } from "./reliability";

/** Captured, for SC-4: a decode (D-271). */
export const CAPTURED_CLASSES = ["successful_reception"] as const;

/** The satellite's own doing, which says nothing about the station (D-184). */
export const SATELLITE_CLASSES = ["satellite_silent", "satellite_state_indeterminate"] as const;

export interface Today {
  day: string;
  scheduled: number;
  ended: number;
  classified: number;
  byClass: Record<string, number>;
  listeningConfirmed: number;
  awaiting: number;
}

export interface DayFigures {
  day: string;
  settled: boolean;
  passes: number;
  capture: Proportion;
  budget: { allowed: number; spent: number; eligible: number };
}

export interface PopulationAggregates {
  simulated: boolean;
  today: Today;
  days: DayFigures[];
}

export interface Aggregates {
  asOf: string;
  settleMarginS: number;
  measured: PopulationAggregates;
  simulated: PopulationAggregates;
}

function counts(fields: Fields, path: string): Record<string, number> {
  const held = asObject(fields.by_class, `${path}.by_class`);
  return Object.fromEntries(Object.keys(held).map((key) => [key, asNumber(held, key, `${path}.by_class`)]));
}

function decodeToday(value: unknown, path: string): Today {
  const fields = asObject(value, path);
  return {
    day: asString(fields, "day", path),
    scheduled: asNumber(fields, "scheduled", path),
    ended: asNumber(fields, "ended", path),
    classified: asNumber(fields, "classified", path),
    byClass: counts(fields, path),
    listeningConfirmed: asNumber(fields, "listening_confirmed", path),
    awaiting: asNumber(fields, "awaiting", path),
  };
}

function decodeDay(value: unknown, path: string): DayFigures {
  const fields = asObject(value, path);
  const budget = asObject(fields.loss_budget, `${path}.loss_budget`);
  const at = `${path}.loss_budget`;
  return {
    day: asString(fields, "day", path),
    settled: asBoolean(fields, "settled", path),
    passes: asNumber(fields, "passes", path),
    capture: decodeProportion(fields.capture_rate, `${path}.capture_rate`),
    budget: {
      allowed: asNumber(budget, "allowed", at),
      spent: asNumber(budget, "spent", at),
      eligible: asNumber(budget, "eligible", at),
    },
  };
}

function decodePopulation(value: unknown, path: string): PopulationAggregates {
  const fields = asObject(value, path);
  const days = fields.days;
  if (!Array.isArray(days)) {
    throw new DecodeError(`${path}.days`, "an array");
  }
  return {
    simulated: asBoolean(fields, "simulated", path),
    today: decodeToday(fields.today, `${path}.today`),
    days: days.map((one, index) => decodeDay(one, `${path}.days[${String(index)}]`)),
  };
}

export function decodeAggregates(body: unknown): Aggregates {
  const path = "aggregates";
  const fields = asObject(body, path);
  return {
    asOf: asString(fields, "as_of", path),
    settleMarginS: asNumber(fields, "settle_margin_s", path),
    measured: decodePopulation(fields.measured, `${path}.measured`),
    simulated: decodePopulation(fields.simulated, `${path}.simulated`),
  };
}

export async function fetchAggregates(fetcher: Fetcher, signal: AbortSignal): Promise<Aggregates> {
  return decodeAggregates(await getJson(fetcher, "/api/v1/aggregates", "the network's figures", signal));
}

/** Today's classified passes as a reader groups them. */
export function todaysOutcomes(today: Today): { decoded: number; lost: number; satellite: number } {
  const of = (classes: readonly string[]) => classes.reduce((sum, one) => sum + (today.byClass[one] ?? 0), 0);
  const decoded = of(CAPTURED_CLASSES);
  const satellite = of(SATELLITE_CLASSES);
  return { decoded, satellite, lost: today.classified - decoded - satellite };
}

/** "a day", "6 h": how long a pass waits before it is classified. */
export function describeMargin(seconds: number): string {
  if (seconds % 86_400 === 0) {
    const days = seconds / 86_400;
    return days === 1 ? "a day" : `${String(days)} days`;
  }
  const hours = seconds / 3600;
  return hours === 1 ? "an hour" : `${String(Math.round(hours * 10) / 10)} h`;
}

// What a station reported about its recent passes, as last corrected.
//
// One read, /api/v1/observations?station_id=…, newest first. Windows arrive
// widened to whole minutes (D-093) and products as hash and size only, because
// MSP defines no transfer (D-176). An observation is a report; whether a silent
// one was a miss is decided by the platform from heartbeats (rule 7), and
// nothing drawn here stands in for that.

import { decodeItems, getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString } from "./decode";

export interface Reception {
  observationId: string;
  assignmentId: string;
  revision: number;
  stationId: string;
  satelliteId: string;
  startedAt: string;
  endedAt: string;
  outcome: string;
  signalDetected: boolean;
  /** Null when the station measured none. */
  peakSnrDb: number | null;
  products: number;
  simulated: boolean;
}

export function decodeReception(value: unknown, path: string): Reception {
  const fields = asObject(value, path);
  const products = fields.products;
  if (!Array.isArray(products)) {
    throw new Error(`${path}.products: expected an array`);
  }
  return {
    observationId: asString(fields, "observation_id", path),
    assignmentId: asString(fields, "assignment_id", path),
    revision: asNumber(fields, "revision", path),
    stationId: asString(fields, "station_id", path),
    satelliteId: asString(fields, "satellite_id", path),
    startedAt: asString(fields, "started_at", path),
    endedAt: asString(fields, "ended_at", path),
    outcome: asString(fields, "outcome", path),
    signalDetected: asBoolean(fields, "signal_detected", path),
    peakSnrDb: fields.peak_snr_db === null ? null : asNumber(fields, "peak_snr_db", path),
    products: products.length,
    simulated: asBoolean(fields, "simulated", path),
  };
}

export const RECENT_LIMIT = 10;

/** One station's most recent receptions, newest first. */
export async function fetchRecentReceptions(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<Reception[]> {
  const query = new URLSearchParams({ limit: String(RECENT_LIMIT), station_id: stationId });
  const body = await getJson(fetcher, `/api/v1/observations?${query.toString()}`, "the observation list", signal);
  return decodeItems(body, decodeReception).items;
}

/** "decoded", "no signal": the outcome as words, never re-judged here. */
export function outcomeLabel(outcome: string): string {
  return outcome.replaceAll("_", " ");
}

/** "signal, peak 12.3 dB", "signal", or "no signal". */
export function describeSignal(reception: Reception): string {
  if (!reception.signalDetected) {
    return "no signal";
  }
  return reception.peakSnrDb === null ? "signal" : `signal, peak ${reception.peakSnrDb.toFixed(1)} dB`;
}

// Every station's open receive-chain warnings, in one list (D-331).
//
// /api/v1/receive-chain-warnings, open ones by default. Each is a warning as a
// station's own receive-chain read publishes it (D-295), with the station and
// the chain beside it. An empty list is the answer for a network with nothing
// open, and never a placeholder.

import { decodeItems, getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asString } from "./decode";

export interface NetworkWarning {
  warningId: number;
  stationId: string;
  band: string;
  freqMinHz: number;
  freqMaxHz: number;
  raisedAt: string;
  shortfallDb: number;
  simulated: boolean;
}

export function decodeNetworkWarning(value: unknown, path: string): NetworkWarning {
  const fields = asObject(value, path);
  return {
    warningId: asNumber(fields, "warning_id", path),
    stationId: asString(fields, "station_id", path),
    band: asString(fields, "band", path),
    freqMinHz: asNumber(fields, "freq_min_hz", path),
    freqMaxHz: asNumber(fields, "freq_max_hz", path),
    raisedAt: asString(fields, "raised_at", path),
    shortfallDb: asNumber(fields, "shortfall_db", path),
    simulated: asBoolean(fields, "simulated", path),
  };
}

export const WARNINGS_LIMIT = 50;

export async function fetchOpenWarnings(fetcher: Fetcher, signal: AbortSignal): Promise<NetworkWarning[]> {
  const url = `/api/v1/receive-chain-warnings?limit=${String(WARNINGS_LIMIT)}`;
  const body = await getJson(fetcher, url, "the warning list", signal);
  return decodeItems(body, decodeNetworkWarning).items;
}

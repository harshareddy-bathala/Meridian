// A station's receive chains: what each usually hears, and whether it is
// falling short of that.
//
// One read, /api/v1/stations/{id}/receive-chain (D-295). A chain is a
// capability, named by its band and range. A receive-chain warning says the
// chain's signal has stayed short of its own baseline across elevations for
// several passes, before reception fails (D-294). It is never called "health":
// that word is the station's own report (D-013).

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNullableString, asNumber, asObject, asString, type Fields } from "./decode";
import { formatDay, formatFrequency } from "./format";

export interface ChainWarning {
  raisedAt: string;
  shortfallDb: number;
  /** The elevations it fell short at, lowest first. */
  affectedDeg: number[];
  /** Null while the warning is open. */
  clearedAt: string | null;
}

export interface ReceiveChain {
  band: string;
  freqMinHz: number;
  freqMaxHz: number;
  /** Null until the chain has heard enough to be watched. */
  baseline: { trainedFrom: string; trainedUntil: string; bins: number } | null;
  /** Newest first. */
  warnings: ChainWarning[];
}

export interface StationReceiveChains {
  stationId: string;
  simulated: boolean;
  chains: ReceiveChain[];
}

function asArray(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new Error(`${path}.${key}: expected an array`);
  }
  return value;
}

function decodeWarning(value: unknown, path: string): ChainWarning {
  const fields = asObject(value, path);
  return {
    raisedAt: asString(fields, "raised_at", path),
    shortfallDb: asNumber(fields, "shortfall_db", path),
    affectedDeg: asArray(fields, "affected_bins", path).map((bin, index) =>
      asNumber(asObject(bin, `${path}.affected_bins[${String(index)}]`), "elevation_deg", path),
    ),
    clearedAt: asNullableString(fields, "cleared_at", path),
  };
}

function decodeChain(value: unknown, path: string): ReceiveChain {
  const fields = asObject(value, path);
  const baseline = fields.baseline;
  return {
    band: asString(fields, "band", path),
    freqMinHz: asNumber(fields, "freq_min_hz", path),
    freqMaxHz: asNumber(fields, "freq_max_hz", path),
    baseline:
      baseline === null
        ? null
        : (() => {
            const held = asObject(baseline, `${path}.baseline`);
            return {
              trainedFrom: asString(held, "trained_from", `${path}.baseline`),
              trainedUntil: asString(held, "trained_until", `${path}.baseline`),
              bins: asArray(held, "bins", `${path}.baseline`).length,
            };
          })(),
    warnings: asArray(fields, "warnings", path).map((one, index) =>
      decodeWarning(one, `${path}.warnings[${String(index)}]`),
    ),
  };
}

export function decodeReceiveChains(body: unknown): StationReceiveChains {
  const path = "receive_chain";
  const fields = asObject(body, path);
  return {
    stationId: asString(fields, "station_id", path),
    simulated: asBoolean(fields, "simulated", path),
    chains: asArray(fields, "chains", path).map((one, index) => decodeChain(one, `${path}.chains[${String(index)}]`)),
  };
}

export async function fetchReceiveChains(
  fetcher: Fetcher,
  stationId: string,
  signal: AbortSignal,
): Promise<StationReceiveChains> {
  const url = `/api/v1/stations/${encodeURIComponent(stationId)}/receive-chain`;
  return decodeReceiveChains(await getJson(fetcher, url, "receive chain", signal));
}

/** The chain's open warning, if it has one. */
export function openWarning(chain: ReceiveChain): ChainWarning | null {
  return chain.warnings.find((one) => one.clearedAt === null) ?? null;
}

/** "VHF 137–138 MHz". */
export function chainName(chain: ReceiveChain): string {
  const low = formatFrequency(chain.freqMinHz).replace(" MHz", "");
  return `${chain.band.toUpperCase()} ${low}–${formatFrequency(chain.freqMaxHz)}`;
}

/** One sentence a station owner can check against the rows behind it. */
export function describeChain(chain: ReceiveChain): string {
  const open = openWarning(chain);
  if (open !== null) {
    const elevations = open.affectedDeg.map((one) => `${String(one)}°`).join(", ");
    return (
      `Receive-chain warning since ${formatDay(open.raisedAt)}: recent passes were ` +
      `${open.shortfallDb.toFixed(1)} dB weaker than this chain usually hears, at ${elevations}.`
    );
  }
  if (chain.baseline === null) {
    return "Not watched yet: it has not heard enough passes to know its usual signal.";
  }
  const last = chain.warnings[0];
  const since =
    last?.clearedAt === undefined || last.clearedAt === null ? "" : ` The last warning cleared ${formatDay(last.clearedAt)}.`;
  return (
    `Hearing as usual, against its signal from ${formatDay(chain.baseline.trainedFrom)} to ` +
    `${formatDay(chain.baseline.trainedUntil)} at ${String(chain.baseline.bins)} elevations.${since}`
  );
}

// Where each tracked satellite is, and where it goes next (D-361).
//
// /api/v1/satellites/tracks: a point a minute for ninety minutes, sampled on
// the platform by the orbit service. The browser propagates nothing: between
// two points it draws a straight line, which at a minute's spacing is within a
// few kilometres of the ground track at the scale a world map is read at.

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asOneOf, asString, DecodeError } from "./decode";

export interface TrackPoint {
  /** Milliseconds since the epoch, so a point is compared without parsing. */
  t: number;
  latDeg: number;
  lonDeg: number;
}

export interface SatelliteTrack {
  satelliteId: string;
  name: string;
  isActive: boolean;
  elementSetEpoch: string;
  elementSetAgeS: number;
  elementSetSource: string;
  track: TrackPoint[];
}

export const OMISSION_REASONS = ["no_element_set", "propagation_failed", "over_limit"] as const;

export interface OmittedSatellite {
  satelliteId: string;
  name: string;
  reason: (typeof OMISSION_REASONS)[number];
}

export interface SatelliteTracks {
  at: string;
  satellites: SatelliteTrack[];
  omitted: OmittedSatellite[];
}

function decodePoint(value: unknown, path: string): TrackPoint {
  const fields = asObject(value, path);
  const t = Date.parse(asString(fields, "t", path));
  if (Number.isNaN(t)) {
    throw new DecodeError(`${path}.t`, "an instant");
  }
  return { t, latDeg: asNumber(fields, "lat_deg", path), lonDeg: asNumber(fields, "lon_deg", path) };
}

function asArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new DecodeError(path, "an array");
  }
  return value;
}

function decodeTrack(value: unknown, path: string): SatelliteTrack {
  const fields = asObject(value, path);
  return {
    satelliteId: asString(fields, "satellite_id", path),
    name: asString(fields, "name", path),
    isActive: asBoolean(fields, "is_active", path),
    elementSetEpoch: asString(fields, "element_set_epoch", path),
    elementSetAgeS: asNumber(fields, "element_set_age_s", path),
    elementSetSource: asString(fields, "element_set_source", path),
    track: asArray(fields.track, `${path}.track`).map((one, index) =>
      decodePoint(one, `${path}.track[${String(index)}]`),
    ),
  };
}

export function decodeTracks(body: unknown): SatelliteTracks {
  const path = "tracks";
  const fields = asObject(body, path);
  return {
    at: asString(fields, "at", path),
    satellites: asArray(fields.satellites, `${path}.satellites`).map((one, index) =>
      decodeTrack(one, `${path}.satellites[${String(index)}]`),
    ),
    omitted: asArray(fields.omitted, `${path}.omitted`).map((one, index) => {
      const at = `${path}.omitted[${String(index)}]`;
      const held = asObject(one, at);
      return {
        satelliteId: asString(held, "satellite_id", at),
        name: asString(held, "name", at),
        reason: asOneOf(held, "reason", OMISSION_REASONS, at),
      };
    }),
  };
}

export async function fetchTracks(fetcher: Fetcher, signal: AbortSignal): Promise<SatelliteTracks> {
  return decodeTracks(await getJson(fetcher, "/api/v1/satellites/tracks", "the satellite tracks", signal));
}

/** Longitude east into -180..180. */
function wrap(lonDeg: number): number {
  return ((((lonDeg + 180) % 360) + 360) % 360) - 180;
}

/**
 * Where the satellite is at ``nowMs``, between the two points around it; null
 * outside the track, where nothing was sampled. Longitude is interpolated the
 * short way round, so a satellite crossing the antimeridian does not sweep
 * back across the map.
 */
export function positionAt(track: TrackPoint[], nowMs: number): { latDeg: number; lonDeg: number } | null {
  for (let index = 1; index < track.length; index += 1) {
    const before = track[index - 1];
    const after = track[index];
    if (before !== undefined && after !== undefined && before.t <= nowMs && nowMs <= after.t) {
      const share = after.t === before.t ? 0 : (nowMs - before.t) / (after.t - before.t);
      const dLon = wrap(after.lonDeg - before.lonDeg);
      return {
        latDeg: before.latDeg + (after.latDeg - before.latDeg) * share,
        lonDeg: wrap(before.lonDeg + dLon * share),
      };
    }
  }
  return null;
}

/**
 * The track as lines a map can draw: split wherever it crosses the
 * antimeridian, so no line is drawn across the whole map between +179° and
 * -179°. Only the part from ``nowMs`` on is kept, since the rest is behind it.
 */
export function trackLines(track: TrackPoint[], nowMs: number): [number, number][][] {
  // From the last point at or before now, so the line starts under the satellite.
  const passed = track.filter((one) => one.t <= nowMs).length;
  const ahead = track.slice(Math.max(0, passed - 1));
  const lines: [number, number][][] = [];
  let current: [number, number][] = [];
  for (const point of ahead) {
    const last = current[current.length - 1];
    if (last !== undefined && Math.abs(point.lonDeg - last[1]) > 180) {
      lines.push(current);
      current = [];
    }
    current.push([point.latDeg, point.lonDeg]);
  }
  if (current.length > 1) {
    lines.push(current);
  }
  return lines.filter((line) => line.length > 1);
}

/** "orbit 6 h old", "orbit 2.4 days old", with the warning a stale one deserves. */
export function describeOrbitAge(ageS: number, staleDays: number): string {
  const days = ageS / 86_400;
  const age = days < 1 ? `orbit ${String(Math.max(0, Math.round(ageS / 3600)))} h old` : `orbit ${days.toFixed(1)} days old`;
  return days > staleDays ? `${age}: its position may be off by hundreds of kilometres` : age;
}

const OMISSION_WORDS: Record<OmittedSatellite["reason"], string> = {
  no_element_set: "no orbit is held for it",
  propagation_failed: "its orbit could not be propagated",
  over_limit: "the catalogue is longer than one map draws",
};

/** "Meteor-M2-4 is not drawn: no orbit is held for it." */
export function describeOmission(one: OmittedSatellite): string {
  return `${one.name} is not drawn: ${OMISSION_WORDS[one.reason]}.`;
}

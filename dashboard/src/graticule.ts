// The lines of latitude and longitude the map draws itself (D-092).
//
// Pure geometry, so it is tested without Leaflet or a browser. Each line is a
// list of [lat, lon] points dense enough that Web Mercator's curvature is not
// drawn as a polygon.

export type LatLon = [number, number];

/** The equator and prime meridian, then every 30°, then every 10°. */
export type GraticuleWeight = "primary" | "major" | "minor";

export interface GraticuleLine {
  points: LatLon[];
  weight: GraticuleWeight;
}

/** Web Mercator cannot show the poles; Leaflet clips at about 85°. */
const MAX_LAT = 85;

function range(from: number, to: number, step: number): number[] {
  const values: number[] = [];
  for (let value = from; value <= to; value += step) {
    values.push(value);
  }
  return values;
}

function weightOf(degrees: number): GraticuleWeight {
  if (degrees === 0) {
    return "primary";
  }
  return degrees % 30 === 0 ? "major" : "minor";
}

/**
 * Ten degrees apart: at the zoom a fitted map of a region lands on, lines 30°
 * apart leave a screen with none on it, and the graticule is the only reference
 * a tile-less map has.
 */
export function graticule(stepDeg = 10, sampleDeg = 5): GraticuleLine[] {
  const parallels = range(-80, 80, stepDeg).map((lat) => ({
    points: range(-180, 180, sampleDeg).map((lon): LatLon => [lat, lon]),
    weight: weightOf(lat),
  }));
  const meridians = range(-180, 180, stepDeg).map((lon) => ({
    points: range(-MAX_LAT, MAX_LAT, sampleDeg).map((lat): LatLon => [lat, lon]),
    weight: weightOf(lon),
  }));
  return [...parallels, ...meridians];
}

/** The steps a scene's graticule may take, in hundredths of a degree, as the
 * published picture's do (D-373). */
export const SCENE_STEPS = [1, 2, 5, 10, 20, 50, 100] as const;

/** At most this many lines across either side of a scene. */
const SCENE_LINES = 5;

/** How far a value may sit from a multiple and still be on it. */
const EDGE = 1e-9;

/** Each multiple of ``step`` hundredths of a degree in ``[low, high]``. */
function multiples(low: number, high: number, step: number): number[] {
  const first = Math.ceil((low * 100) / step - EDGE);
  const last = Math.floor((high * 100) / step + EDGE);
  const values: number[] = [];
  for (let k = first; k <= last; k += 1) values.push((k * step) / 100);
  return values;
}

/**
 * The finest step, in hundredths of a degree, that draws at most five lines a
 * side of ``[west, south, east, north]``: the rule the published picture's
 * graticule is drawn by (ingest's ``interval_hundredths``), so the map and the
 * picture put their lines in the same places.
 */
export function sceneStep(bbox: [number, number, number, number]): number {
  const [west, south, east, north] = bbox;
  for (const step of SCENE_STEPS) {
    const most = Math.max(multiples(south, north, step).length, multiples(west, east, step).length);
    if (most <= SCENE_LINES) return step;
  }
  return SCENE_STEPS[SCENE_STEPS.length - 1] ?? 100;
}

export interface SceneLine {
  points: LatLon[];
  /** "lat" for a parallel, "lon" for a meridian. */
  axis: "lat" | "lon";
  /** The degree the line is drawn at. */
  value: number;
  /** How many decimals its label needs: two for 0.01° steps, none for 1°. */
  decimals: number;
}

/** The parallels and then the meridians inside a scene's box, at its step. */
export function sceneGraticule(bbox: [number, number, number, number]): SceneLine[] {
  const [west, south, east, north] = bbox;
  const step = sceneStep(bbox);
  const decimals = step < 10 ? 2 : step < 100 ? 1 : 0;
  return [
    ...multiples(south, north, step).map(
      (lat): SceneLine => ({ axis: "lat", value: lat, decimals, points: [[lat, west], [lat, east]] }),
    ),
    ...multiples(west, east, step).map(
      (lon): SceneLine => ({ axis: "lon", value: lon, decimals, points: [[south, lon], [north, lon]] }),
    ),
  ];
}

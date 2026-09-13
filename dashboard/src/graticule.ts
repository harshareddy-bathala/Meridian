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

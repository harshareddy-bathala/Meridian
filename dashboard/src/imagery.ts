// Published open imagery, as /api/v1/imagery serves it (D-376).
//
// The listing is every scene of a published area, newest acquisition first,
// with what a gallery card needs. One scene is its manifest
// (`imagery-manifest-1`): the box, the zooms, every marker with the records it
// cites, the caption and the files. A scene's files are served at the base its
// detail names, same-origin, so the page's `img-src 'self'` covers them.
//
// Every scene here is public archive imagery, credited. None is received by
// Meridian's own stations yet (Stage 40), and the platform labels any scene
// simulated, which the page badges (rule 5). A marker with no citation is
// refused as it is decoded: a marker that cites nothing is not one the page
// may show.

import { getJson, type Fetcher } from "./api";
import {
  asBoolean,
  asNumber,
  asObject,
  asOneOf,
  asString,
  DecodeError,
  type Fields,
} from "./decode";

export const SOURCE_CLASSES = ["high_resolution", "older_record", "daily_wide"] as const;
export type SourceClass = (typeof SOURCE_CLASSES)[number];

/** What each class of archive is, in a reader's words. */
export const CLASS_LABEL: Record<SourceClass, string> = {
  high_resolution: "Sharpest recent view",
  older_record: "Longer Landsat record",
  daily_wide: "Daily wide view",
};

export interface ImageryScene {
  hash: string;
  areaId: number;
  areaLabel: string;
  sourceClass: SourceClass;
  sourceId: string;
  platform: string;
  instrument: string;
  acquiredAt: string;
  publishedAt: string;
  cloudFraction: number | null;
  /** The picture's caption, which is also its alt text. */
  caption: string;
  credit: string;
  /** The display picture, same-origin. */
  display: string;
  sizeBytes: number;
  simulated: boolean;
}

export type ImageryRead = { scenes: ImageryScene[] } | { notYetComputed: string };

export interface Citation {
  sourceId: string;
  recordId: string;
  url: string;
  licence: string;
}

export type MarkerGeometry =
  | { type: "point"; latDeg: number; lonDeg: number }
  | { type: "box"; bbox: [number, number, number, number] };

export const MARKER_KINDS = ["area", "change", "fire", "place"] as const;
export type MarkerKind = (typeof MARKER_KINDS)[number];

export interface SceneMarker {
  /** Its number on the picture and on the map: its place in the manifest, from 1. */
  number: number;
  kind: MarkerKind;
  geometry: MarkerGeometry;
  label: string;
  text: string;
  citations: Citation[];
}

export interface SceneFile {
  path: string;
  sha256: string;
  sizeBytes: number;
}

export interface SceneManifest {
  areaId: number;
  areaLabel: string;
  sourceId: string;
  sourceClass: SourceClass;
  platform: string;
  instrument: string;
  acquiredAt: string;
  /** [west, south, east, north], in degrees. */
  bounds: [number, number, number, number];
  resolutionM: number;
  cloudFraction: number | null;
  cloudMethod: string;
  rendering: string;
  zoomMin: number;
  zoomMax: number;
  credit: string;
  licence: string;
  caption: string;
  markers: SceneMarker[];
  files: SceneFile[];
}

export interface SceneDetail {
  hash: string;
  publishedAt: string;
  /** Every listed file is at `base` + its path. */
  base: string;
  simulated: boolean;
  manifest: SceneManifest;
}

export type SceneRead = { scene: SceneDetail } | { notYetComputed: string };

function list(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new DecodeError(`${path}.${key}`, "an array");
  }
  return value;
}

function box(value: unknown, path: string): [number, number, number, number] {
  if (!Array.isArray(value) || value.length !== 4 || !value.every((one) => typeof one === "number")) {
    throw new DecodeError(path, "[west, south, east, north]");
  }
  const [west, south, east, north] = value as [number, number, number, number];
  return [west, south, east, north];
}

function nullableFraction(fields: Fields, key: string, path: string): number | null {
  return fields[key] === null ? null : asNumber(fields, key, path);
}

function decodeScene(value: unknown, path: string): ImageryScene {
  const fields = asObject(value, path);
  return {
    hash: asString(fields, "hash", path),
    areaId: asNumber(fields, "area_id", path),
    areaLabel: asString(fields, "area_label", path),
    sourceClass: asOneOf(fields, "source_class", SOURCE_CLASSES, path),
    sourceId: asString(fields, "source_id", path),
    platform: asString(fields, "platform", path),
    instrument: asString(fields, "instrument", path),
    acquiredAt: asString(fields, "acquired_at", path),
    publishedAt: asString(fields, "published_at", path),
    cloudFraction: nullableFraction(fields, "cloud_fraction", path),
    caption: asString(fields, "caption", path),
    credit: asString(fields, "credit", path),
    display: asString(fields, "display", path),
    sizeBytes: asNumber(fields, "size_bytes", path),
    simulated: asBoolean(fields, "simulated", path),
  };
}

export function decodeImagery(body: unknown): ImageryRead {
  const path = "imagery";
  const fields = asObject(body, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  return {
    scenes: list(fields, "items", path).map((one, index) => decodeScene(one, `${path}.items[${String(index)}]`)),
  };
}

function decodeCitation(value: unknown, path: string): Citation {
  const fields = asObject(value, path);
  return {
    sourceId: asString(fields, "source_id", path),
    recordId: asString(fields, "record_id", path),
    url: asString(fields, "url", path),
    licence: asString(fields, "licence", path),
  };
}

function decodeGeometry(value: unknown, path: string): MarkerGeometry {
  const fields = asObject(value, path);
  const type = asOneOf(fields, "type", ["point", "box"] as const, path);
  if (type === "point") {
    return { type, latDeg: asNumber(fields, "lat_deg", path), lonDeg: asNumber(fields, "lon_deg", path) };
  }
  return { type, bbox: box(fields.bbox, `${path}.bbox`) };
}

function decodeMarker(value: unknown, index: number, path: string): SceneMarker {
  const fields = asObject(value, path);
  const citations = list(fields, "citations", path).map((one, at) =>
    decodeCitation(one, `${path}.citations[${String(at)}]`),
  );
  if (citations.length === 0) {
    throw new DecodeError(`${path}.citations`, "at least one citation");
  }
  return {
    number: index + 1,
    kind: asOneOf(fields, "kind", MARKER_KINDS, path),
    geometry: decodeGeometry(fields.geometry, `${path}.geometry`),
    label: asString(fields, "label", path),
    text: asString(fields, "text", path),
    citations,
  };
}

function decodeFile(value: unknown, path: string): SceneFile {
  const fields = asObject(value, path);
  return {
    path: asString(fields, "path", path),
    sha256: asString(fields, "sha256", path),
    sizeBytes: asNumber(fields, "size_bytes", path),
  };
}

function decodeManifest(value: unknown, path: string): SceneManifest {
  const fields = asObject(value, path);
  return {
    areaId: asNumber(fields, "area_id", path),
    areaLabel: asString(fields, "area_label", path),
    sourceId: asString(fields, "source_id", path),
    sourceClass: asOneOf(fields, "source_class", SOURCE_CLASSES, path),
    platform: asString(fields, "platform", path),
    instrument: asString(fields, "instrument", path),
    acquiredAt: asString(fields, "acquired_at", path),
    bounds: box(fields.bounds, `${path}.bounds`),
    resolutionM: asNumber(fields, "resolution_m", path),
    cloudFraction: nullableFraction(fields, "cloud_fraction", path),
    cloudMethod: asString(fields, "cloud_method", path),
    rendering: asString(fields, "rendering", path),
    zoomMin: asNumber(fields, "zoom_min", path),
    zoomMax: asNumber(fields, "zoom_max", path),
    credit: asString(fields, "credit", path),
    licence: asString(fields, "licence", path),
    caption: asString(fields, "caption", path),
    markers: list(fields, "annotations", path).map((one, index) =>
      decodeMarker(one, index, `${path}.annotations[${String(index)}]`),
    ),
    files: list(fields, "files", path).map((one, index) => decodeFile(one, `${path}.files[${String(index)}]`)),
  };
}

export function decodeSceneDetail(body: unknown): SceneRead {
  const path = "scene";
  const fields = asObject(body, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  return {
    scene: {
      hash: asString(fields, "hash", path),
      publishedAt: asString(fields, "published_at", path),
      base: asString(fields, "base", path),
      simulated: asBoolean(fields, "simulated", path),
      manifest: decodeManifest(fields.manifest, `${path}.manifest`),
    },
  };
}

/** The gallery: the first page, newest acquisition first. */
export async function fetchImagery(fetcher: Fetcher, signal: AbortSignal): Promise<ImageryRead> {
  return decodeImagery(await getJson(fetcher, "/api/v1/imagery", "the published imagery", signal));
}

/**
 * An area's latest scene of each class, one request a class, so a daily view
 * published every day never crowds the sharper ones out. A class with no scene
 * yet is left out; a deployment that publishes no imagery says so.
 */
export async function fetchAreaScenes(fetcher: Fetcher, areaId: number, signal: AbortSignal): Promise<ImageryRead> {
  const reads = await Promise.all(
    SOURCE_CLASSES.map(async (one) => {
      const url = `/api/v1/imagery?area=${String(areaId)}&source_class=${one}&limit=1`;
      return decodeImagery(await getJson(fetcher, url, "the area's imagery", signal));
    }),
  );
  const closed = reads.find((one) => "notYetComputed" in one);
  if (closed !== undefined) return closed;
  return { scenes: reads.flatMap((one) => ("scenes" in one ? one.scenes : [])) };
}

/** How long a scene counts as newly published on the changes page. */
export const NEW_FOR_DAYS = 7;

/** The scenes published in the last week, the most recently published first. */
export function newlyPublished(scenes: ImageryScene[], now: Date, days: number = NEW_FOR_DAYS): ImageryScene[] {
  const since = now.getTime() - days * 86_400_000;
  return scenes
    .filter((one) => Date.parse(one.publishedAt) >= since)
    .sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
}

/** One scene. Not in the routes file: the warmer reads each from the listing. */
export async function fetchScene(fetcher: Fetcher, hash: string, signal: AbortSignal): Promise<SceneRead> {
  const url = `/api/v1/imagery/${encodeURIComponent(hash)}`;
  return decodeSceneDetail(await getJson(fetcher, url, "the scene", signal));
}

/** The cloud over the area's box, in words; a source that states none says so. */
export function cloudLabel(fraction: number | null): string {
  if (fraction === null) return "No cloud share stated";
  if (fraction === 0) return "No cloud over the area";
  if (fraction < 0.01) return "Under 1% cloud over the area";
  return `${String(Math.round(fraction * 100))}% cloud over the area`;
}

/** "12.97°N", "77.59°E": a coordinate as the picture labels it. */
export function formatLat(lat: number, decimals = 4): string {
  return `${Math.abs(lat).toFixed(decimals)}°${lat >= 0 ? "N" : "S"}`;
}

export function formatLon(lon: number, decimals = 4): string {
  return `${Math.abs(lon).toFixed(decimals)}°${lon >= 0 ? "E" : "W"}`;
}

export interface Download {
  label: string;
  path: string;
  url: string;
  /** Null for the manifest, which lists the others' sizes and not its own. */
  sizeBytes: number | null;
}

const DOWNLOADS: [string, string][] = [
  ["scene.tif", "GeoTIFF, EPSG:4326, which a GIS tool opens in place"],
  ["scene.png", "PNG of the scene's own pixels"],
  ["scene.pgw", "World file for the PNG"],
  ["display.png", "The picture above, with its graticule and markers"],
];

/** Every file a reader may keep, as the manifest lists it, and the manifest. */
export function downloads(scene: SceneDetail): Download[] {
  const listed = DOWNLOADS.flatMap(([path, label]) => {
    const file = scene.manifest.files.find((one) => one.path === path);
    return file === undefined ? [] : [{ label, path, url: `${scene.base}${path}`, sizeBytes: file.sizeBytes }];
  });
  const manifest = {
    label: "Manifest: every input, file and marker, by its digest",
    path: "manifest.json",
    url: `${scene.base}manifest.json`,
    sizeBytes: null,
  };
  return [...listed, manifest];
}

/** The scene's own tiles, same-origin, as Leaflet asks for them. */
export function tileTemplate(scene: SceneDetail): string {
  return `${scene.base}tiles/{z}/{x}/{y}.webp`;
}

/** A citation's address, if it is one a link may go to: https, or the API's own. */
export function citationHref(citation: Citation): string | null {
  return citation.url.startsWith("https://") || citation.url.startsWith("/api/v1/") ? citation.url : null;
}

/** The scene's source, as a card names it: "Sentinel-2B MSI, sharpest recent view". */
export function sourceLine(
  platform: string,
  instrument: string,
  sourceClass: SourceClass,
  simulated: boolean,
): string {
  if (simulated) return SIMULATED_SOURCE;
  return `${platform} ${instrument}, ${CLASS_LABEL[sourceClass].toLowerCase()}`;
}

/** What a simulated scene is, wherever a source would be named (rule 5, D-378). */
export const SIMULATED_SOURCE = "Synthetic pixels for the demonstration, not a satellite's";
export const ARCHIVE_KIND = "Public archive imagery, credited.";
export const SIMULATED_KIND = "Simulated, for the demonstration: synthetic pixels, not a satellite's.";

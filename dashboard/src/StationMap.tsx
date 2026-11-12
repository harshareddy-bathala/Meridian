import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useCallback, useEffect, useRef, useState } from "react";

import { livenessLabel, precisionLabel } from "./format";
import { graticule } from "./graticule";
import { drawPositions, drawTracks } from "./satelliteLayer";
import type { Station } from "./stations";
import type { SatelliteTrack } from "./tracks";

const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION = "© OpenStreetMap contributors";

type TileStatus = "loading" | "loaded" | "failed" | "disabled";

function tileUrl(): string {
  return import.meta.env.VITE_MAP_TILE_URL ?? OSM_TILES;
}

function drawGraticule(map: L.Map): void {
  for (const line of graticule()) {
    L.polyline(line.points, {
      className: `graticule graticule-${line.weight}`,
      interactive: false,
    }).addTo(map);
  }
}

/** What a tile request tells the tile server about us: the origin, and no more. */
export const TILE_REFERRER_POLICY = "strict-origin";

function addTiles(map: L.Map, setStatus: (status: TileStatus) => void): void {
  const url = tileUrl();
  if (url === "") {
    return;
  }
  let loaded = false;
  // The page sends no referrer (D-208), and OpenStreetMap refuses a browser's
  // tile request without one, answering each with an "access blocked" image.
  // So the tiles alone send the dashboard's origin, never a path (D-336).
  L.tileLayer(url, { attribution: OSM_ATTRIBUTION, maxZoom: 12, referrerPolicy: TILE_REFERRER_POLICY })
    .on("tileload", () => {
      loaded = true;
      setStatus("loaded");
    })
    .on("tileerror", () => {
      // One failed tile among loaded ones is a gap, not an outage.
      if (!loaded) {
        setStatus("failed");
      }
    })
    .addTo(map);
}

interface MapView {
  map: L.Map;
  markers: L.FeatureGroup;
  tracks: L.FeatureGroup;
  positions: L.FeatureGroup;
}

export function useLeafletMap() {
  const [view, setView] = useState<MapView | null>(null);
  const [tileStatus, setTileStatus] = useState<TileStatus>(() =>
    tileUrl() === "" ? "disabled" : "loading",
  );
  // A callback ref rather than an effect: the map belongs to the element's
  // lifetime, and React 19 runs the returned cleanup when the element goes.
  const attach = useCallback((node: HTMLDivElement) => {
    const map = L.map(node, { worldCopyJump: true }).setView([20, 0], 2);
    drawGraticule(map);
    addTiles(map, setTileStatus);
    // Tracks under the stations, positions over them: a station stays clickable.
    const tracks = L.featureGroup().addTo(map);
    const markers = L.featureGroup().addTo(map);
    setView({ map, markers, tracks, positions: L.featureGroup().addTo(map) });
    return () => {
      setView(null);
      map.remove();
    };
  }, []);
  return { attach, view, tileStatus };
}

function tooltipFor(station: Station): HTMLElement {
  // Built from text nodes: a station's name is whatever its operator typed.
  const element = document.createElement("div");
  const lines = [
    station.name,
    station.simulated ? "Simulated station" : "Physical station",
    livenessLabel(station.liveness),
    `Location published ${precisionLabel(station.locationPrecisionDecimals)}`,
  ];
  for (const text of lines) {
    element.appendChild(document.createElement("div")).textContent = text;
  }
  return element;
}

/** The world a satellite can be over, as far as a Mercator map can draw it. */
const WORLD: L.LatLngTuple[] = [
  [-75, -180],
  [80, 180],
];

function useStationMarkers(
  view: MapView | null,
  stations: Station[],
  onSelect: (stationId: string) => void,
  fit: "stations" | "world",
) {
  const fitted = useRef(false);
  // Through a ref, so a new callback identity does not redraw every marker.
  const select = useRef(onSelect);
  useEffect(() => {
    select.current = onSelect;
  }, [onSelect]);
  useEffect(() => {
    if (view === null) {
      return;
    }
    const { map, markers } = view;
    markers.clearLayers();
    for (const station of stations) {
      const kind = station.simulated ? "marker-simulated" : "marker-physical";
      L.circleMarker([station.latDeg, station.lonDeg], {
        radius: 7,
        className: `marker ${kind} liveness-${station.liveness}`,
      })
        .bindTooltip(tooltipFor(station))
        .on("click", () => {
          select.current(station.stationId);
        })
        .addTo(markers);
    }
    // Once: after that the reader owns the view, and a refresh must not move it.
    // A map with satellites shows the world: a satellite is seldom over the
    // stations, and one fitted to them would leave it off the edge.
    const bounds = fit === "world" ? L.latLngBounds(WORLD) : markers.getBounds();
    if (!fitted.current && bounds.isValid()) {
      fitted.current = true;
      // Capped at 4 so a lone station is shown with its region around it, and
      // a tile-less map still has graticule lines on screen.
      map.fitBounds(bounds, { maxZoom: 4, padding: [32, 32] });
    }
  }, [view, stations, fit]);
}

/** The satellites over the map, when the view draws them; see satelliteLayer.ts. */
export interface Satellites {
  tracks: SatelliteTrack[];
  nowMs: number;
}

function useSatellites(view: MapView | null, satellites: Satellites | undefined) {
  const tracks = satellites?.tracks;
  const nowMs = satellites?.nowMs ?? 0;
  const minute = Math.floor(nowMs / 60_000);
  useEffect(() => {
    if (view !== null && tracks !== undefined) {
      drawTracks(view.tracks, tracks, minute * 60_000);
    }
  }, [view, tracks, minute]);
  useEffect(() => {
    if (view !== null && tracks !== undefined) {
      drawPositions(view.positions, tracks, nowMs);
    }
  }, [view, tracks, nowMs]);
}

const TILE_NOTE: Partial<Record<TileStatus, string>> = {
  failed: "Map tiles could not be loaded.",
  disabled: "This build draws no map tiles.",
};

/** Why the map has no tiles, and that ``drawn`` are on the graticule alone; undefined when it has them. */
export function tileNote(status: TileStatus, drawn: string): string | undefined {
  const note = TILE_NOTE[status];
  return note === undefined ? undefined : `${note} ${drawn} are drawn on the graticule alone.`;
}

interface MapProps {
  stations: Station[];
  onSelect: (stationId: string) => void;
  /** Satellites to draw over the stations; the live page's map has them. */
  satellites?: Satellites | undefined;
}

export function StationMap({ stations, onSelect, satellites }: MapProps) {
  const { attach, view, tileStatus } = useLeafletMap();
  useStationMarkers(view, stations, onSelect, satellites === undefined ? "stations" : "world");
  useSatellites(view, satellites);
  const note = tileNote(tileStatus, "Stations");
  return (
    <figure className="station-map">
      <div ref={attach} className="station-map-canvas" role="img"
        aria-label={satellites === undefined ? "Map of stations" : "Map of stations, and of satellites with their next ninety minutes"}
      />
      {note !== undefined && <figcaption className="dim">{note}</figcaption>}
    </figure>
  );
}

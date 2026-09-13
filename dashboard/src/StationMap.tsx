import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useCallback, useEffect, useRef, useState } from "react";

import { livenessLabel, precisionLabel } from "./format";
import { graticule } from "./graticule";
import type { Station } from "./stations";

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

function addTiles(map: L.Map, setStatus: (status: TileStatus) => void): void {
  const url = tileUrl();
  if (url === "") {
    return;
  }
  let loaded = false;
  L.tileLayer(url, { attribution: OSM_ATTRIBUTION, maxZoom: 12 })
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
}

function useLeafletMap() {
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
    setView({ map, markers: L.featureGroup().addTo(map) });
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

function useStationMarkers(view: MapView | null, stations: Station[]) {
  const fitted = useRef(false);
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
        .addTo(markers);
    }
    // Once: after that the reader owns the view, and a refresh must not move it.
    const bounds = markers.getBounds();
    if (!fitted.current && bounds.isValid()) {
      fitted.current = true;
      // Capped at 4 so a lone station is shown with its region around it, and
      // a tile-less map still has graticule lines on screen.
      map.fitBounds(bounds, { maxZoom: 4, padding: [32, 32] });
    }
  }, [view, stations]);
}

const TILE_NOTE: Partial<Record<TileStatus, string>> = {
  failed: "Map tiles could not be loaded. Stations are drawn on the graticule alone.",
  disabled: "This build draws no map tiles. Stations are drawn on the graticule alone.",
};

export function StationMap({ stations }: { stations: Station[] }) {
  const { attach, view, tileStatus } = useLeafletMap();
  useStationMarkers(view, stations);
  const note = TILE_NOTE[tileStatus];
  return (
    <figure className="station-map">
      <div ref={attach} className="station-map-canvas" role="img" aria-label="Map of stations" />
      {note !== undefined && <figcaption className="dim">{note}</figcaption>}
    </figure>
  );
}

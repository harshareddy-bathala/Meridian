import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useCallback, useEffect, useRef, useState } from "react";

import { sceneGraticule } from "./graticule";
import { formatLat, formatLon, type SceneDetail, type SceneMarker, tileTemplate } from "./imagery";

// One published scene on a map of its own (D-377): the scene's own tiles,
// same-origin and bounded to it, under the dashboard's own graticule and every
// marker as a layer. Nothing here is drawn from text as HTML: a label, a place
// name and a citation are whatever their records say, so each is set as text.
//
// The tiles are the scene; the graticule and the markers are drawn from the
// manifest. So when the tiles cannot be loaded — the platform away and the
// front holding no copy, which it never keeps for files (D-376) — the map
// still says where everything is, and the page says the picture is missing.

export type TileStatus = "loading" | "loaded" | "failed";

interface SceneMapProps {
  scene: SceneDetail;
  /** The marker chosen, by its number, or null. */
  selected: number | null;
  onSelect: (marker: number) => void;
  onTiles: (status: TileStatus) => void;
}

function label(text: string): HTMLElement {
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}

function markerLayer(marker: SceneMarker, selected: boolean): L.Layer {
  const className = `scene-marker scene-marker-${marker.kind}${selected ? " scene-marker-selected" : ""}`;
  if (marker.geometry.type === "box") {
    const [west, south, east, north] = marker.geometry.bbox;
    return L.rectangle(
      [
        [south, west],
        [north, east],
      ],
      { className, fill: false },
    );
  }
  return L.circleMarker([marker.geometry.latDeg, marker.geometry.lonDeg], {
    radius: marker.kind === "fire" ? 6 : 5,
    className,
  });
}

function sceneBounds(scene: SceneDetail): L.LatLngBounds {
  const [west, south, east, north] = scene.manifest.bounds;
  return L.latLngBounds([south, west], [north, east]);
}

/**
 * Each line, labelled at the scene's edge as the published picture is: a
 * latitude at its west end, a longitude at its south end, so no label sits on
 * a marker inside the scene.
 */
function drawGraticule(map: L.Map, scene: SceneDetail): void {
  const [west, south] = scene.manifest.bounds;
  for (const line of sceneGraticule(scene.manifest.bounds)) {
    L.polyline(line.points, { className: "graticule graticule-scene", interactive: false }).addTo(map);
    const lat = line.axis === "lat";
    const text = lat ? formatLat(line.value, line.decimals) : formatLon(line.value, line.decimals);
    L.tooltip({ permanent: true, direction: lat ? "right" : "top", className: "graticule-label" })
      .setLatLng(lat ? [line.value, west] : [south, line.value])
      .setContent(label(text))
      .addTo(map);
  }
}

function addTiles(map: L.Map, scene: SceneDetail, onTiles: (status: TileStatus) => void): void {
  let loaded = false;
  L.tileLayer(tileTemplate(scene), {
    bounds: sceneBounds(scene),
    minZoom: scene.manifest.zoomMin,
    maxNativeZoom: scene.manifest.zoomMax,
    maxZoom: scene.manifest.zoomMax + 2,
    attribution: scene.manifest.credit,
  })
    .on("tileload", () => {
      loaded = true;
      onTiles("loaded");
    })
    .on("tileerror", () => {
      if (!loaded) onTiles("failed");
    })
    .addTo(map);
}

export function SceneMap({ scene, selected, onSelect, onTiles }: SceneMapProps) {
  const [map, setMap] = useState<{ map: L.Map; markers: L.FeatureGroup } | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const select = useRef(onSelect);
  const tiles = useRef(onTiles);
  useEffect(() => {
    select.current = onSelect;
    tiles.current = onTiles;
  }, [onSelect, onTiles]);
  const attach = useCallback(
    (node: HTMLDivElement) => {
      const bounds = sceneBounds(scene);
      const view = L.map(node, { maxBounds: bounds.pad(0.25), maxBoundsViscosity: 1 });
      view.fitBounds(bounds);
      addTiles(view, scene, (status) => {
        tiles.current(status);
      });
      drawGraticule(view, scene);
      view.on("mousemove", (event: L.LeafletMouseEvent) => {
        setCursor(`${formatLat(event.latlng.lat)} ${formatLon(event.latlng.lng)}`);
      });
      view.on("mouseout", () => {
        setCursor(null);
      });
      setMap({ map: view, markers: L.featureGroup().addTo(view) });
      return () => {
        setMap(null);
        view.remove();
      };
    },
    // One map per scene: a new scene is a new element, keyed by its hash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  useEffect(() => {
    if (map === null) return;
    map.markers.clearLayers();
    for (const marker of scene.manifest.markers) {
      markerLayer(marker, marker.number === selected)
        .bindTooltip(label(`${String(marker.number)}. ${marker.label}`))
        .on("click", () => {
          select.current(marker.number);
        })
        .addTo(map.markers);
    }
  }, [map, scene, selected]);
  return (
    <figure className="scene-map">
      <div
        ref={attach}
        className="scene-map-canvas"
        role="region"
        aria-label={`Map of the scene of ${scene.manifest.areaLabel}, with its markers; every marker is also in the table below`}
      />
      <figcaption className="dim scene-cursor" aria-live="off">
        {cursor === null ? "Point at the map to read its latitude and longitude." : `Under the pointer: ${cursor}`}
      </figcaption>
    </figure>
  );
}

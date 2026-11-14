import * as L from "leaflet";
import { useEffect, useRef } from "react";

import type { Area } from "./areas";
import { tileNote, useLeafletMap } from "./StationMap";

// Each area as its bounding box, on the same map the stations are drawn on: a
// box, never the polygon, which D-326 keeps unpublished. Choosing a box opens
// the area.

function tooltipFor(area: Area): HTMLElement {
  // Built from text nodes: a label is whatever the team typed.
  const element = document.createElement("div");
  for (const text of [area.label, `${area.areaKm2.toFixed(0)} km²`]) {
    element.appendChild(document.createElement("div")).textContent = text;
  }
  return element;
}

interface AreaMapProps {
  areas: Area[];
  selected: number | null;
  onSelect: (areaId: number) => void;
}

export function AreaMap({ areas, selected, onSelect }: AreaMapProps) {
  const { attach, view, tileStatus } = useLeafletMap();
  const fitted = useRef(false);
  const select = useRef(onSelect);
  useEffect(() => {
    select.current = onSelect;
  }, [onSelect]);
  useEffect(() => {
    if (view === null) return;
    view.markers.clearLayers();
    for (const area of areas) {
      const [west, south, east, north] = area.bbox;
      L.rectangle(
        [
          [south, west],
          [north, east],
        ],
        { className: area.areaId === selected ? "area-box area-box-selected" : "area-box" },
      )
        .bindTooltip(tooltipFor(area))
        .on("click", () => {
          select.current(area.areaId);
        })
        .addTo(view.markers);
    }
    const bounds = view.markers.getBounds();
    if (!fitted.current && bounds.isValid()) {
      fitted.current = true;
      view.map.fitBounds(bounds, { maxZoom: 8, padding: [32, 32] });
    }
  }, [view, areas, selected]);
  const note = tileNote(tileStatus, "Areas");
  return (
    <figure className="station-map">
      <div ref={attach} className="station-map-canvas" role="region" aria-label="Map of the areas of interest" />
      {note !== undefined && <figcaption className="dim">{note}</figcaption>}
    </figure>
  );
}

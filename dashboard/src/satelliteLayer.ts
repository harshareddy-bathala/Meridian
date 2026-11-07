// The satellites over the station map: each one's next ninety minutes as a
// line, and where it is now as a mark (D-361). Plain Leaflet calls, kept out of
// the map component so it stays one reviewable file.

import * as L from "leaflet";

import { STALE_ORBIT_DAYS } from "./stationSchedule";
import { describeOrbitAge, positionAt, trackLines, type SatelliteTrack } from "./tracks";

function tooltipFor(satellite: SatelliteTrack): HTMLElement {
  // Built from text nodes, as the station tooltip is.
  const element = document.createElement("div");
  const lines = [
    satellite.name,
    describeOrbitAge(satellite.elementSetAgeS, STALE_ORBIT_DAYS),
    satellite.isActive ? "Expected to be transmitting" : "Believed silent",
  ];
  for (const text of lines) {
    element.appendChild(document.createElement("div")).textContent = text;
  }
  return element;
}

/** Redraw every track from ``nowMs`` on. Called when the tracks or the minute change. */
export function drawTracks(group: L.FeatureGroup, tracks: SatelliteTrack[], nowMs: number): void {
  group.clearLayers();
  for (const satellite of tracks) {
    for (const line of trackLines(satellite.track, nowMs)) {
      L.polyline(line, { className: "track", interactive: false }).addTo(group);
    }
  }
}

/** Redraw where each satellite is at ``nowMs``. Called every tick. */
export function drawPositions(group: L.FeatureGroup, tracks: SatelliteTrack[], nowMs: number): void {
  group.clearLayers();
  for (const satellite of tracks) {
    const here = positionAt(satellite.track, nowMs);
    if (here === null) {
      continue;
    }
    const stale = satellite.elementSetAgeS / 86_400 > STALE_ORBIT_DAYS;
    // A diamond, never a circle: circles are stations, and a hollow dashed
    // circle is already a simulated one.
    L.marker([here.latDeg, here.lonDeg], {
      icon: L.divIcon({ className: `satellite${stale ? " satellite-stale" : ""}`, iconSize: [12, 12] }),
      keyboard: false,
    })
      .bindTooltip(tooltipFor(satellite))
      .addTo(group);
  }
}

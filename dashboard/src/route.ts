// Which view the dashboard shows, from the URL fragment.
//
// A fragment never reaches the server, which is how D-091 gives the dashboard
// views without a catch-all route, and a view is a link that can be shared:
//
//   #/network              the fleet, its reliability and its open warnings
//   #/stations[/<id>]      the station directory, and one station's panel
//   #/schedule[/<id>]      one station's next hours, and why
//   #/losses               every diagnosed loss
//   #/insights             the conditions outside, and the model's calibration
//   #/kiosk                every view in turn, for a screen nobody touches
//
// Anything else is the network view, so an old or mistyped link still lands
// somewhere true.

export const VIEWS = ["network", "stations", "schedule", "losses", "insights", "kiosk"] as const;
export type View = (typeof VIEWS)[number];

export interface Route {
  view: View;
  /** The station a station or schedule view is about; null for the others. */
  stationId: string | null;
}

export const DEFAULT_ROUTE: Route = { view: "network", stationId: null };

export function parseRoute(hash: string): Route {
  const [name = "", ...rest] = hash.replace(/^#\/?/, "").split("/");
  const view = VIEWS.find((one) => one === name);
  if (view === undefined) {
    return DEFAULT_ROUTE;
  }
  const raw = rest.join("/");
  const takesStation = view === "stations" || view === "schedule";
  return { view, stationId: takesStation && raw !== "" ? decodeURIComponent(raw) : null };
}

export function formatRoute(route: Route): string {
  const station = route.stationId === null ? "" : `/${encodeURIComponent(route.stationId)}`;
  return `#/${route.view}${station}`;
}

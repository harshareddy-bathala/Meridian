// Which view the dashboard shows, from the URL fragment.
//
// A fragment never reaches the server, which is how D-091 gives the dashboard
// views without a catch-all route, and a view is a link that can be shared:
//
//   #/live                 what is happening now: the home page
//   #/stations[/<id>]      the station directory, and one station's panel
//   #/schedule[/<id>]      one station's next hours, and why
//   #/results              every settled pass, the thirty days, every loss
//   #/insights             the conditions outside, and the model's calibration
//   #/kiosk                every view in turn, for a screen nobody touches
//
// Anything else is the live view, so an old or mistyped link still lands
// somewhere true. Two views Stage 37 replaced keep their links: `#/network`
// opens the live view and `#/losses` the results it is now part of.

export const VIEWS = ["live", "stations", "schedule", "results", "insights", "kiosk"] as const;
export type View = (typeof VIEWS)[number];

export interface Route {
  view: View;
  /** The station a station or schedule view is about; null for the others. */
  stationId: string | null;
}

export const DEFAULT_ROUTE: Route = { view: "live", stationId: null };

/** Views Stage 37 replaced, and the views that took them in (D-364, D-365). */
const RENAMED: Record<string, View> = { network: "live", losses: "results" };

export function parseRoute(hash: string): Route {
  const [given = "", ...rest] = hash.replace(/^#\/?/, "").split("/");
  const name = RENAMED[given] ?? given;
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

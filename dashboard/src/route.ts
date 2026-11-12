// Which view the dashboard shows, from the URL fragment.
//
// A fragment never reaches the server, which is how D-091 gives the dashboard
// views without a catch-all route, and a view is a link that can be shared:
//
//   #/live                 what is happening now: the home page
//   #/stations[/<id>]      the station directory, and one station's panel
//   #/schedule[/<id>]      one station's next hours, and why
//   #/results              every settled pass, the thirty days, every loss
//   #/areas[/<id>]         the published areas of interest, and one area
//   #/changes              every change to an area, the network's events, releases
//   #/reports              the network's week in words, area reports, the evaluation
//   #/kiosk                every view in turn, for a screen nobody touches
//
// Anything else is the live view, so an old or mistyped link still lands
// somewhere true. Views Stage 37 replaced keep their links: `#/network` opens
// the live view, `#/losses` the results it is now part of, and `#/insights` the
// reports, which took in its model (its conditions are on the live view).

export const VIEWS = [
  "live",
  "stations",
  "schedule",
  "results",
  "areas",
  "changes",
  "reports",
  "kiosk",
] as const;
export type View = (typeof VIEWS)[number];

export interface Route {
  view: View;
  /** The station a station or schedule view is about; null for the others. */
  stationId: string | null;
  /** The area an areas view is about; absent for every other view. */
  areaId?: number;
}

export const DEFAULT_ROUTE: Route = { view: "live", stationId: null };

/** Views Stage 37 replaced, and the views that took them in (D-364, D-365, D-367). */
const RENAMED: Record<string, View> = { network: "live", losses: "results", insights: "reports" };

/** The id a view's route carries, from what follows its name. */
function withId(view: View, raw: string): Route {
  if (view === "areas") {
    // An area's id is a whole number; anything else is the list of areas.
    return /^\d+$/.test(raw) ? { view, stationId: null, areaId: Number(raw) } : { view, stationId: null };
  }
  const takesStation = view === "stations" || view === "schedule";
  return { view, stationId: takesStation && raw !== "" ? decodeURIComponent(raw) : null };
}

export function parseRoute(hash: string): Route {
  const [given = "", ...rest] = hash.replace(/^#\/?/, "").split("/");
  const name = RENAMED[given] ?? given;
  const view = VIEWS.find((one) => one === name);
  return view === undefined ? DEFAULT_ROUTE : withId(view, rest.join("/"));
}

export function formatRoute(route: Route): string {
  const station = route.stationId === null ? "" : `/${encodeURIComponent(route.stationId)}`;
  const area = route.areaId === undefined ? "" : `/${String(route.areaId)}`;
  return `#/${route.view}${station}${area}`;
}

import { describe, expect, it } from "vitest";

import { DEFAULT_ROUTE, formatRoute, parseRoute } from "./route";

describe("the route in the fragment", () => {
  it("names each view, and a station where one belongs", () => {
    expect(parseRoute("#/losses")).toEqual({ view: "losses", stationId: null });
    expect(parseRoute("#/schedule/st_1")).toEqual({ view: "schedule", stationId: "st_1" });
    expect(parseRoute("#/stations/st_1")).toEqual({ view: "stations", stationId: "st_1" });
    expect(parseRoute("#/insights/st_1")).toEqual({ view: "insights", stationId: null });
  });

  it("lands an empty, old or mistyped fragment on the live view", () => {
    expect(DEFAULT_ROUTE).toEqual({ view: "live", stationId: null });
    expect(parseRoute("")).toEqual(DEFAULT_ROUTE);
    expect(parseRoute("#/")).toEqual(DEFAULT_ROUTE);
    expect(parseRoute("#/nowhere")).toEqual(DEFAULT_ROUTE);
  });

  it("opens the live view from the old home page's link", () => {
    expect(parseRoute("#/network")).toEqual(DEFAULT_ROUTE);
    expect(parseRoute("#/live")).toEqual(DEFAULT_ROUTE);
  });

  it("round-trips a station id that needs encoding", () => {
    const route = { view: "schedule" as const, stationId: "st a/b" };
    expect(formatRoute(route)).toBe("#/schedule/st%20a%2Fb");
    expect(parseRoute(formatRoute(route))).toEqual(route);
  });
});

import { describe, expect, it } from "vitest";

import routesFile from "../../deploy/front/snapshot-routes.txt?raw";
import type { Fetcher } from "./api";
import {
  compassPoint,
  decodePlan,
  describeOmission,
  expectedDecodes,
  fetchPlan,
  formatPlace,
  passesBySatellite,
  placeProblem,
  planUrl,
  roundPlace,
} from "./plan";
import { formatRoute, parseRoute } from "./route";

const PASS = {
  satellite_id: "norad:59051",
  aos: "2026-11-14T10:02:00Z",
  los: "2026-11-14T10:15:00Z",
  max_elevation_deg: 64,
  max_elevation_at: "2026-11-14T10:08:00Z",
  aos_azimuth_deg: 12,
  los_azimuth_deg: 188,
  predicted_yield: { status: "computed", probability: 0.81 },
};

function body(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: "computed",
    at: "2026-11-14T09:37:00Z",
    until: "2026-11-16T09:37:00Z",
    place: { lat_deg: 12.3, lon_deg: 77.6, alt_m: 0 },
    min_elevation_deg: 10,
    yield_model: {
      status: "computed",
      configuration: "A",
      population: "own",
      model_sha256: "ab".repeat(32),
      as_of: "2026-11-01T00:00:00Z",
    },
    satellites: [
      {
        satellite_id: "norad:59051",
        name: "Meteor-M2-4",
        downlinks: [{ centre_freq_hz: 137_900_000, mode: "lrpt" }],
        element_set_epoch: "2026-11-13T12:00:00Z",
        element_set_age_s: 77_820,
        element_set_source: "celestrak",
      },
    ],
    passes: [
      PASS,
      {
        ...PASS,
        aos: "2026-11-14T21:40:00Z",
        predicted_yield: { status: "computed", probability: 0.3 },
      },
    ],
    omitted: [
      {
        satellite_id: "norad:57166",
        name: "Meteor-M2-3",
        reason: "no_element_set",
      },
    ],
    ...overrides,
  };
}

describe("the place a reader gives", () => {
  it("is sent to a tenth of a degree, and never as -0", () => {
    expect(roundPlace({ latDeg: 12.345678, lonDeg: 77.654321 })).toEqual({
      latDeg: 12.3,
      lonDeg: 77.7,
    });
    expect(planUrl({ latDeg: -0.04, lonDeg: -0.01 })).toBe("/api/v1/plan?lat=0.0&lon=0.0");
    expect(planUrl({ latDeg: 12.345678, lonDeg: -77.654321 })).toBe("/api/v1/plan?lat=12.3&lon=-77.7");
  });

  it("is refused in words when it is off the globe or not a number", () => {
    expect(placeProblem("12.9", "77.6")).toBeNull();
    expect(placeProblem("-90", "180")).toBeNull();
    expect(placeProblem("", "77.6")).toMatch(/Latitude/);
    expect(placeProblem("91", "77.6")).toMatch(/Latitude/);
    expect(placeProblem("north", "77.6")).toMatch(/Latitude/);
    expect(placeProblem("12.9", "-180.5")).toMatch(/Longitude/);
    expect(placeProblem("12.9", " ")).toMatch(/Longitude/);
  });

  it("is written as a reader writes one", () => {
    expect(formatPlace({ latDeg: 12.3, lonDeg: 77.6 })).toBe("12.3° N, 77.6° E");
    expect(formatPlace({ latDeg: -33.9, lonDeg: -70.7 })).toBe("33.9° S, 70.7° W");
  });

  it("never reaches the fragment, so it stays out of the browser's history", () => {
    expect(parseRoute("#/plan")).toEqual({ view: "plan", stationId: null });
    expect(parseRoute("#/plan/12.3/77.6")).toEqual({
      view: "plan",
      stationId: null,
    });
    expect(formatRoute({ view: "plan", stationId: null })).toBe("#/plan");
  });

  it("is asked of the platform directly, and the front warms and keeps no copy of it", async () => {
    const asked: string[] = [];
    const fetcher: Fetcher = (url) => {
      asked.push(url);
      return Promise.resolve(new Response(JSON.stringify(body())));
    };
    await fetchPlan(fetcher, { latDeg: 12.34, lonDeg: 77.56 }, new AbortController().signal);

    expect(asked).toEqual(["/api/v1/plan?lat=12.3&lon=77.6"]);
    expect(routesFile).not.toMatch(/\/api\/v1\/plan/);
  });
});

describe("the planner's answer", () => {
  it("decodes every pass, its yield and the model it came from", () => {
    const plan = decodePlan(body());

    expect(plan.place).toEqual({ latDeg: 12.3, lonDeg: 77.6 });
    expect(plan.passes[0]?.predictedYield).toEqual({ probability: 0.81 });
    expect(plan.yieldModel).toEqual({
      configuration: "A",
      population: "own",
      modelSha256: "ab".repeat(32),
      asOf: "2026-11-01T00:00:00Z",
    });
    expect(plan.satellites[0]?.downlinks).toEqual([{ centreFreqHz: 137_900_000, mode: "lrpt" }]);
    expect(describeOmission(plan.omitted[0] ?? { satelliteId: "", name: "", reason: "over_limit" })).toBe(
      "Meteor-M2-3 was not searched: no orbit is held for it.",
    );
  });
});

describe("what the page works out from it", () => {
  it("expects the sum of the yields, and nothing at all when they are not measured", () => {
    const plan = decodePlan(body());
    expect(expectedDecodes(plan.passes)).toBeCloseTo(1.11);
    expect(expectedDecodes([])).toBe(0);

    const unmeasured = decodePlan(
      body({
        yield_model: {
          status: "not_yet_computed",
          reason: "No model.",
          available_from_stage: 37,
        },
        passes: [
          {
            ...PASS,
            predicted_yield: { status: "not_measured", reason: "No model." },
          },
        ],
      }),
    );
    expect(unmeasured.yieldModel).toEqual({ notYetComputed: "No model." });
    expect(unmeasured.passes[0]?.predictedYield).toEqual({
      notMeasured: "No model.",
    });
    expect(expectedDecodes(unmeasured.passes)).toBeNull();
  });

  it("groups the passes under the satellite they belong to", () => {
    const grouped = passesBySatellite(decodePlan(body()));
    expect(grouped.get("norad:59051")).toHaveLength(2);
  });

  it("refuses an answer it does not understand rather than drawing it", () => {
    expect(() => decodePlan(body({ status: "not_yet_computed" }))).toThrow();
    expect(() => decodePlan(body({ passes: [{ ...PASS, predicted_yield: { status: "maybe" } }] }))).toThrow();
    expect(() => decodePlan(body({ omitted: [{ satellite_id: "x", name: "x", reason: "bored" }] }))).toThrow();
  });

  it("names the eighth of the compass a pass rises and sets in", () => {
    expect([0, 22, 23, 90, 188, 315, 359, 360, -10].map(compassPoint)).toEqual([
      "N",
      "N",
      "NE",
      "E",
      "S",
      "NW",
      "N",
      "N",
      "N",
    ]);
  });
});

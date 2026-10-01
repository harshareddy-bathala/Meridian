import { describe, expect, it } from "vitest";

import { decodeStationCapture, describeCapture } from "./reliability";

function population(stations: unknown[]) {
  return { simulated: false, passes: 0, stations };
}

function station(id: string, numerator: number, denominator: number) {
  return {
    station_id: id,
    capture: {
      numerator,
      denominator,
      estimate: denominator === 0 ? null : numerator / denominator,
      interval: denominator === 0 ? null : [0.79, 0.92],
    },
  };
}

const body = {
  status: "computed",
  window_start: "2026-09-01T00:00:00Z",
  window_end: "2026-10-01T00:00:00Z",
  measured: population([station("st_real", 41, 47)]),
  simulated: population([station("st_sim", 3, 4)]),
};

describe("decodeStationCapture", () => {
  it("finds a measured station among the measured", () => {
    const found = decodeStationCapture(body, "st_real");

    expect(found?.simulated).toBe(false);
    expect(found?.capture.numerator).toBe(41);
  });

  it("labels a station found among the simulated as simulated", () => {
    expect(decodeStationCapture(body, "st_sim")?.simulated).toBe(true);
  });

  it("is null for a station nothing was counted for", () => {
    expect(decodeStationCapture(body, "st_new")).toBeNull();
  });

  it("names the field a renamed key broke", () => {
    const broken = { ...body, measured: population([{ station_id: "st_real", capture: { numerator: 1 } }]) };

    expect(() => decodeStationCapture(broken, "st_real")).toThrow("capture.denominator");
  });
});

describe("describeCapture", () => {
  it("states the rate with its interval and its count", () => {
    const found = decodeStationCapture(body, "st_real");

    expect(found && describeCapture(found.capture)).toBe("0.87 [0.79, 0.92], 41 of 47 passes");
  });

  it("says nothing was counted rather than drawing a zero", () => {
    expect(describeCapture({ numerator: 0, denominator: 0, estimate: null, interval: null })).toBe(
      "no pass settled in the window",
    );
  });
});

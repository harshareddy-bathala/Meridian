import { describe, expect, it } from "vitest";

import { averageCoverage, decodeUptime, uptimeBars } from "./uptime";

const body = {
  station_id: "st_1",
  simulated: true,
  heartbeat_interval_s: 30,
  hours: [
    { hour: "2026-09-29T04:00:00Z", heartbeats: 0, listening: 0, coverage: 0 },
    { hour: "2026-09-29T05:00:00Z", heartbeats: 120, listening: 40, coverage: 1 },
  ],
};

describe("decodeUptime", () => {
  it("reads every hour, oldest first", () => {
    const uptime = decodeUptime(body);

    expect(uptime.simulated).toBe(true);
    expect(uptime.hours.map((one) => one.heartbeats)).toEqual([0, 120]);
  });

  it("names the field a renamed key broke", () => {
    expect(() => decodeUptime({ ...body, hours: [{ hour: "x", heartbeats: 1 }] })).toThrow("coverage");
  });
});

describe("the uptime strip", () => {
  it("draws each hour as tall as it was covered", () => {
    const [empty, full] = uptimeBars(decodeUptime(body).hours, 100, 20);

    expect(empty?.height).toBe(0);
    expect(full).toEqual({ x: 50, y: 0, width: 49, height: 20 });
  });

  it("averages the hours it was given", () => {
    expect(averageCoverage(decodeUptime(body).hours)).toBe(0.5);
    expect(averageCoverage([])).toBe(0);
  });
});

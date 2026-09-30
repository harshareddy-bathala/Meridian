import { describe, expect, it } from "vitest";

import type { Fetcher } from "./api";
import { decodeProfiles, fetchProfiles, horizonPath, skyPoint } from "./profiles";

const signal = new AbortController().signal;

const body = {
  station_id: "st_1",
  simulated: true,
  declared: [
    {
      built_at: "2026-09-29T07:00:00Z",
      bins: [
        { azimuth_deg: 90, azimuth_width_deg: 225, min_elevation_deg: 8 },
        { azimuth_deg: 315, azimuth_width_deg: 135, min_elevation_deg: 30 },
      ],
    },
  ],
  learned: {
    method: "d159-v1",
    dataset_sha256: "ab".repeat(32),
    trained_from: "2026-08-01T00:00:00Z",
    trained_until: "2026-09-23T06:00:00Z",
    built_at: "2026-09-29T07:00:00Z",
    bins: [{ azimuth_deg: 40, azimuth_width_deg: 10, min_elevation_deg: 10, sample_count: 5 }],
  },
};

describe("decodeProfiles", () => {
  it("reads the declared and learned horizons apart", () => {
    const profiles = decodeProfiles(body);

    expect(profiles.simulated).toBe(true);
    expect(profiles.declared).toHaveLength(1);
    expect(profiles.declared[0]?.[1]).toEqual({
      azimuthDeg: 315,
      widthDeg: 135,
      minElevationDeg: 30,
      sampleCount: null,
    });
    expect(profiles.learned?.bins[0]?.sampleCount).toBe(5);
  });

  it("accepts a station with nothing learned yet", () => {
    expect(decodeProfiles({ ...body, learned: null }).learned).toBeNull();
  });

  it("names the field a renamed key broke", () => {
    expect(() => decodeProfiles({ ...body, simulated: "yes" })).toThrow("profiles.simulated");
  });

  it("asks for the station's own profiles", async () => {
    const urls: string[] = [];
    const fetcher: Fetcher = (url) => {
      urls.push(url);
      return Promise.resolve(new Response(JSON.stringify(body)));
    };

    await fetchProfiles(fetcher, "st 1", signal);

    expect(urls).toEqual(["/api/v1/stations/st%201/profiles"]);
  });
});

describe("the sky plot", () => {
  it("puts north up, east right, and the zenith at the centre", () => {
    const north = skyPoint(0, 0, 100);
    const east = skyPoint(90, 0, 100);
    const zenith = skyPoint(123, 90, 100);

    expect(north[0]).toBeCloseTo(0);
    expect(north[1]).toBeCloseTo(-100);
    expect(east[0]).toBeCloseTo(100);
    expect(east[1]).toBeCloseTo(0);
    expect(zenith[0]).toBeCloseTo(0);
    expect(zenith[1]).toBeCloseTo(0);
  });

  it("draws a floor below the horizon at the edge", () => {
    expect(skyPoint(0, -90, 100)[1]).toBeCloseTo(-100);
  });

  it("draws nothing for no bins, and a closed outline otherwise", () => {
    expect(horizonPath([], 100)).toBe("");
    const path = horizonPath([{ azimuthDeg: 0, widthDeg: 360, minElevationDeg: 45, sampleCount: null }], 100);
    expect(path.startsWith("M0.0,-50.0")).toBe(true);
    expect(path.endsWith("Z")).toBe(true);
  });
});

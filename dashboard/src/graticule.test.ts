import { describe, expect, it } from "vitest";

import { graticule, type GraticuleLine } from "./graticule";

const isParallel = (line: GraticuleLine) =>
  line.points.every(([lat]) => lat === line.points[0]?.[0]);

describe("graticule", () => {
  const lines = graticule();
  const parallels = lines.filter(isParallel);
  const meridians = lines.filter((line) => !isParallel(line));

  it("draws parallels every 10° from 80°S to 80°N and meridians all the way round", () => {
    expect(parallels.map((line) => line.points[0]?.[0])).toEqual(
      Array.from({ length: 17 }, (_, index) => -80 + index * 10),
    );
    expect(meridians).toHaveLength(37);
  });

  it("weights the equator and prime meridian, then every 30°, then the rest", () => {
    const weightAt = (degrees: number) =>
      parallels.find((line) => line.points[0]?.[0] === degrees)?.weight;

    expect([weightAt(0), weightAt(30), weightAt(-60), weightAt(10)]).toEqual([
      "primary",
      "major",
      "major",
      "minor",
    ]);
    expect(lines.filter((line) => line.weight === "primary")).toHaveLength(2);
  });

  it("stays inside what Web Mercator can draw", () => {
    const latitudes = lines.flatMap((line) => line.points.map(([lat]) => lat));

    expect(Math.max(...latitudes)).toBe(85);
    expect(Math.min(...latitudes)).toBe(-85);
  });
});

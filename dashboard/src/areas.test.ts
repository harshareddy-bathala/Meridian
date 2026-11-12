// The areas of interest, decoded and put into words (D-326, D-366).

import { describe, expect, it } from "vitest";

import { decodeAreas, describeInterval, formatValue, quantityWords, seriesByQuantity, verdictWords } from "./areas";
import { areasBody } from "./fixtures.test-data";

describe("the areas read", () => {
  it("says why nothing is published, with no figure to draw", () => {
    const read = decodeAreas({ status: "not_yet_computed", reason: "None yet (D-326).", available_from_stage: 34 });
    expect(read).toEqual({ notYetComputed: "None yet (D-326)." });
  });

  it("decodes the summary, its gaps kept as gaps", () => {
    const read = decodeAreas(areasBody);
    if (!("summary" in read)) throw new Error("expected a summary");
    const ndvi = seriesByQuantity(read.summary.series, 1).get("ndvi") ?? [];
    expect(ndvi.map((one) => one.periodFrom.slice(0, 10))).toEqual(["2026-10-05", "2026-10-21", "2026-11-01"]);
    expect(ndvi[2]?.value).toBeNull();
    expect(ndvi[2]?.missingReason).toBe("no granule covered the area");
    expect(seriesByQuantity(read.summary.series, 2).size).toBe(0);
  });

  it("refuses a box that is not four numbers", () => {
    const bad = structuredClone(areasBody);
    bad.summary.areas[0] = { ...bad.summary.areas[0], bbox: [1, 2, 3] } as (typeof bad.summary.areas)[0];
    expect(() => decodeAreas(bad)).toThrow("bbox");
  });
});

describe("an area in words", () => {
  it("names each quantity, and its unit unless it has none", () => {
    expect(quantityWords("cloud_cover")).toBe("Cloud cover");
    expect(quantityWords("sea_ice")).toBe("sea ice");
    expect(formatValue(0.41234, "1")).toBe("0.412");
    expect(formatValue(12.54, "mm")).toBe("12.5 mm");
  });

  it("gives a change with its interval, and a verdict in words", () => {
    const read = decodeAreas(areasBody);
    if (!("summary" in read)) throw new Error("expected a summary");
    const [change] = read.summary.changes;
    if (change === undefined) throw new Error("expected a change");
    expect(describeInterval(change)).toBe("-0.0244 [-0.05, 0.03]");
    expect(describeInterval({ ...change, change: null })).toBe("no change measured");
    expect(verdictWords("insufficient")).toBe("Too few values to say");
  });
});

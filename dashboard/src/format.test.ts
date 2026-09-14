import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatFrequency,
  formatWindow,
  livenessLabel,
  precisionLabel,
} from "./format";

const now = new Date("2026-09-13T12:00:00Z");

describe("formatAge", () => {
  it.each([
    ["2026-09-13T12:00:02Z", "just now"],
    ["2026-09-13T11:59:18Z", "42 s ago"],
    ["2026-09-13T11:53:00Z", "7 min ago"],
    ["2026-09-13T09:00:00Z", "3 h ago"],
    ["2026-09-01T12:00:00Z", "12 d ago"],
  ])("describes a heartbeat at %s as %s", (iso, expected) => {
    expect(formatAge(iso, now)).toBe(expected);
  });

  it("says never for a station that has not sent one", () => {
    expect(formatAge(null, now)).toBe("never");
  });

  it("does not print NaN for a timestamp it cannot parse", () => {
    expect(formatAge("yesterday", now)).toBe("unknown");
  });
});

describe("precisionLabel", () => {
  it.each([
    [1, "to about 11 km"],
    [2, "to about 1 km"],
    [3, "to about 111 m"],
    [6, "to under 1 m"],
  ])("describes %i decimals as %s", (decimals, expected) => {
    expect(precisionLabel(decimals)).toBe(expected);
  });
});

describe("livenessLabel", () => {
  it("names the state a station has never left", () => {
    expect(livenessLabel("never_seen")).toBe("Never seen");
  });
});

describe("formatWindow", () => {
  it("states a window in UTC whole minutes with its day", () => {
    expect(formatWindow("2026-09-14T06:10:00Z", "2026-09-14T06:22:00Z")).toBe(
      "06:10–06:22 UTC, 14 Sep",
    );
  });
});

describe("formatFrequency", () => {
  it("states a downlink in megahertz to one decimal", () => {
    expect(formatFrequency(137_900_000)).toBe("137.9 MHz");
  });
});

// The live page's data, decoded and put into words (D-361, D-362, D-363).

import { describe, expect, it } from "vitest";

import { decodeAggregates, describeMargin, todaysOutcomes } from "./aggregates";
import { decodeEvent, describeEvent } from "./events";
import { aggregatesBody, assignmentBody, eventBody, tracksBody } from "./fixtures.test-data";
import { formatCountdown, formatMinute, formatUtcClock } from "./format";
import { decodeAssignment, nextPass, passesUnderWay } from "./schedule";
import { decodeTracks, describeOmission, describeOrbitAge, positionAt, trackLines } from "./tracks";

const T0 = Date.parse("2026-11-07T14:00:00Z");

describe("a satellite's track", () => {
  const tracks = decodeTracks(tracksBody);
  const [meteor] = tracks.satellites;
  if (meteor === undefined) throw new Error("expected a satellite");

  it("places the satellite between the two points around now", () => {
    expect(positionAt(meteor.track, T0 + 90_000)).toEqual({ latDeg: 13, lonDeg: -176 });
  });

  it("crosses the antimeridian the short way round", () => {
    const halfway = positionAt(meteor.track, T0 + 30_000);
    expect(halfway?.latDeg).toBe(11);
    expect(Math.abs(halfway?.lonDeg ?? 0)).toBeCloseTo(180);
  });

  it("is not placed outside what was sampled", () => {
    expect(positionAt(meteor.track, T0 - 1)).toBeNull();
    expect(positionAt(meteor.track, T0 + 180_001)).toBeNull();
  });

  it("is drawn as lines split at the antimeridian, from now on", () => {
    expect(trackLines(meteor.track, T0)).toEqual([[[12, -178], [14, -174]]]);
    expect(trackLines(meteor.track, T0 + 60_000)).toEqual([[[12, -178], [14, -174]]]);
  });

  it("says how old its orbit is, and warns when it is stale", () => {
    expect(describeOrbitAge(21_600, 3)).toBe("orbit 6 h old");
    expect(describeOrbitAge(4 * 86_400, 3)).toContain("off by hundreds of kilometres");
  });

  it("names a satellite it cannot draw, and why", () => {
    expect(tracks.omitted.map(describeOmission)).toEqual(["Meteor-M2-4 is not drawn: no orbit is held for it."]);
  });
});

describe("today's passes", () => {
  const aggregates = decodeAggregates(aggregatesBody);

  it("are grouped into decoded, lost and the satellite's own", () => {
    expect(todaysOutcomes(aggregates.simulated.today)).toEqual({ decoded: 7, lost: 2, satellite: 1 });
    expect(todaysOutcomes(aggregates.measured.today)).toEqual({ decoded: 0, lost: 0, satellite: 0 });
  });

  it("say how long a pass waits to be classified", () => {
    expect(describeMargin(86_400)).toBe("a day");
    expect(describeMargin(3600)).toBe("an hour");
    expect(describeMargin(5400)).toBe("1.5 h");
  });
});

describe("the events feed", () => {
  it("puts each kind into words", () => {
    const words = ["station_joined", "pass_decoded", "loss_diagnosed", "warning_raised", "warning_cleared"].map(
      (kind) => describeEvent(decodeEvent(eventBody(kind), "e")),
    );
    expect(words).toEqual([
      "joined the network",
      "decoded a pass of norad:57166",
      "lost a pass of norad:57166: something blocked the sky",
      "receive chain fell short of its baseline",
      "receive chain recovered",
    ]);
  });

  it("refuses a kind it does not know", () => {
    expect(() => decodeEvent(eventBody("station_left"), "e")).toThrow("e.kind");
  });
});

describe("the network's passes", () => {
  const passes = ["as_1", "as_2"].map((id, index) =>
    decodeAssignment(
      {
        ...assignmentBody(id),
        start_at: new Date(T0 + index * 600_000).toISOString(),
        end_at: new Date(T0 + index * 600_000 + 480_000).toISOString(),
      },
      id,
    ),
  );

  it("are under way between their start and their end", () => {
    expect(passesUnderWay(passes, T0 + 60_000).map((one) => one.assignmentId)).toEqual(["as_1"]);
    expect(passesUnderWay(passes, T0 + 540_000)).toEqual([]);
  });

  it("give the next one to begin", () => {
    expect(nextPass(passes, T0 + 60_000)?.assignmentId).toBe("as_2");
    expect(nextPass(passes, T0 + 600_000)).toBeNull();
  });
});

describe("the clock", () => {
  it("is UTC, to the second", () => {
    expect(formatUtcClock(new Date("2026-11-07T09:03:07Z"))).toBe("09:03:07 UTC");
    expect(formatMinute("2026-11-07T14:03:12Z")).toBe("14:03 UTC, 7 Nov");
  });

  it("counts down in the unit that matters", () => {
    expect(formatCountdown(42_000)).toBe("in 42 s");
    expect(formatCountdown(425_000)).toBe("in 7 min 05 s");
    expect(formatCountdown(8_040_000)).toBe("in 2 h 14 min");
    expect(formatCountdown(-5_000)).toBe("in 0 s");
  });
});

import { describe, expect, it } from "vitest";

import type { Fetcher } from "./api";
import { decodeConditions, describeSample } from "./conditions";
import { assignmentBody, insightsBody, kpSample, lossBody, runBody, warningBody } from "./fixtures.test-data";
import { decodeInsights } from "./insights";
import { causeWords, decodeLoss, whyUndetermined } from "./losses";
import { decodeNetworkWarning } from "./networkWarnings";
import { decodeScheduleRun, fetchLatestRun } from "./scheduleRun";
import { decodeStationSchedule, describeElementSetAge, elementSetAgeDays } from "./stationSchedule";

describe("a diagnosed loss", () => {
  it("is decoded with every cause tested and its findings", () => {
    const loss = decodeLoss(lossBody, "loss");
    expect(loss.cause).toBe("obstruction");
    expect(loss.tested.map((one) => one.fired)).toEqual([true, false]);
    expect(loss.tested[0]?.findings).toEqual({ lost_inside: 9, share: 0.41 });
    expect(loss.tested[1]?.findings.state).toBeNull();
    expect(whyUndetermined(loss)).toBeNull();
  });

  it("says why it is undetermined, in words", () => {
    const none = decodeLoss({ ...lossBody, cause: "undetermined", tested: [lossBody.tested[1]] }, "loss");
    const both = decodeLoss(
      {
        ...lossBody,
        cause: "undetermined",
        tested: [lossBody.tested[0], { ...lossBody.tested[0], cause: "interference" }],
      },
      "loss",
    );
    expect(whyUndetermined(none)).toBe("No test found its evidence: 1 causes tested, none fired.");
    expect(whyUndetermined(both)).toContain("the noise floor was raised");
    expect(causeWords("station_not_listening")).toBe("the station was not listening");
  });

  it("refuses a cause outside the platform's vocabulary", () => {
    expect(() => decodeLoss({ ...lossBody, cause: "gremlins" }, "loss")).toThrow("loss.cause");
  });
});

describe("the network's warnings", () => {
  it("are decoded with the chain they name", () => {
    const warning = decodeNetworkWarning(warningBody, "w");
    expect([warning.stationId, warning.band, warning.shortfallDb, warning.simulated]).toEqual([
      "st_1",
      "vhf",
      4.2,
      true,
    ]);
  });
});

describe("a station's schedule", () => {
  const body = {
    station_id: "st_1",
    simulated: true,
    start: "2026-11-02T10:00:00Z",
    end: "2026-11-02T16:00:00Z",
    entries: [
      { assignment: assignmentBody("as_1"), max_elevation_deg: 61, element_set_epoch: "2026-10-31T10:04:00Z" },
      {
        assignment: assignmentBody("as_2", "skipped"),
        max_elevation_deg: 23,
        element_set_epoch: "2026-08-11T12:00:00Z",
      },
    ],
  };

  it("is decoded with what each decision weighed and the orbit's age", () => {
    const schedule = decodeStationSchedule(body);
    const [first, second] = schedule.entries;
    expect(first?.assignment.explanation?.weighedAgainst).toHaveLength(1);
    expect(second?.assignment.explanation?.rule).toBe("overlap");
    expect(first && elementSetAgeDays(first)).toBeCloseTo(2);
    expect(first && describeElementSetAge(first)).toBe("orbit 2.0 days old");
    expect(second && describeElementSetAge(second)).toContain("may be off by minutes");
  });
});

describe("the latest round", () => {
  it("is decoded with its gain stated as not measured", () => {
    const run = decodeScheduleRun(runBody);
    expect([run.solverStatus, run.simulatedDecisions, run.baselineGain]).toEqual([
      "optimal",
      30,
      "measured on a snapshot (D-172)",
    ]);
  });

  it("is null before any round decided anything", async () => {
    const fetcher: Fetcher = () =>
      Promise.resolve(new Response(JSON.stringify({ error: "not_found", message: "none" }), { status: 404 }));
    expect(await fetchLatestRun(fetcher, new AbortController().signal)).toBeNull();
  });
});

describe("the conditions", () => {
  it("state a missing Kp in words, never as a zero", () => {
    const conditions = decodeConditions({
      status: "computed",
      as_of: "2026-11-02T10:00:00Z",
      kp: { status: "not_measured", reason: "No Kp has been ingested." },
      cloud_cover: [],
    });
    expect(conditions.kp).toEqual({ notMeasured: "No Kp has been ingested." });
  });

  it("carry each value's source, and a published gap as the gap", () => {
    const conditions = decodeConditions({
      status: "computed",
      as_of: "2026-11-02T10:00:00Z",
      kp: kpSample,
      cloud_cover: [{ ...kpSample, quantity: "cloud_cover", unit: "%", value: null, missing_reason: "gap" }],
    });
    expect("notMeasured" in conditions.kp).toBe(false);
    const [cloud] = conditions.cloudCover;
    expect(cloud && describeSample(cloud, "Cloud")).toBe("Cloud: the source published no value (gap)");
  });
});

describe("the model insight", () => {
  it("is not yet computed until a run is published", () => {
    expect(
      decodeInsights({ status: "not_yet_computed", reason: "No run yet.", available_from_stage: 34 }),
    ).toEqual({ notYetComputed: "No run yet." });
  });

  it("keeps a configuration the archive cannot support as its reason alone", () => {
    const read = decodeInsights(insightsBody);
    if (!("computed" in read)) {
      throw new Error("expected a computed insight");
    }
    const [fitted, refused] = read.computed.configurations;
    expect(fitted?.computable && fitted.bins).toHaveLength(10);
    expect(refused).toEqual({ name: "C", computable: false, reason: "needs a learned station profile" });
    expect(read.computed.label).toContain("not by Meridian");
  });
});

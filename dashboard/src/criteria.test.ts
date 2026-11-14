import { describe, expect, it } from "vitest";

import { type Criterion, type CriterionFigure, decodeCriteria, describeFigure, figureWords, verdictOf } from "./criteria";
import { criteriaBody } from "./fixtures-reports.test-data";

function read() {
  const decoded = decodeCriteria(criteriaBody);
  if (!("computed" in decoded)) {
    throw new Error("expected computed criteria");
  }
  return decoded.computed;
}

function firstFigure(criterion: Criterion | undefined): CriterionFigure {
  const figure = criterion?.status === "measured" ? criterion.figures[0] : undefined;
  if (figure === undefined) {
    throw new Error("expected a measured criterion with a figure");
  }
  return figure;
}

describe("the success criteria", () => {
  it("decodes each criterion as the run states it", () => {
    const [sc4, sc5, sc2] = read().criteria;
    expect(sc4?.status).toBe("measured");
    expect(sc5?.simulated).toBe(true);
    expect(sc2).toEqual({ id: "SC-2", simulated: false, proposed: false, status: "not_measured", reason: "D was not fitted" });
  });

  it("is not yet computed while no summary is named", () => {
    expect(decodeCriteria({ status: "not_yet_computed", reason: "None yet.", available_from_stage: 37 })).toEqual({
      notYetComputed: "None yet.",
    });
  });

  it("says each figure in its unit, against its target, over what it counts", () => {
    const [sc4, sc5] = read().criteria;
    expect(describeFigure(firstFigure(sc4))).toBe("93% (88% to 96%); target at least 90%, over 140 passes");
    expect(describeFigure(firstFigure(sc5))).toBe("68.2 s; target at most 90 s, over 123 timed faults");
    expect(figureWords("recall_timing_fault")).toBe("Recall of timing fault");
  });

  it("gives the run's own verdict, and never claims what it may not", () => {
    expect(read().criteria.map(verdictOf)).toEqual(["Target met, not yet claimed", "Target met", "Not measured"]);
  });
});

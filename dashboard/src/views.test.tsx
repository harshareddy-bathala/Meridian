// Every view that draws a figure from simulated rows marks it (CLAUDE.md rule
// 5), and one drawn from measured rows does not. Rendered to static markup,
// which runs no effect and fetches nothing: each view's presentational parts
// are given simulated data directly.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { assignmentBody, insightsBody, lossBody, runBody, warningBody } from "./fixtures.test-data";
import { decodeInsights } from "./insights";
import { ModelPanel } from "./Insights";
import { decodeLoss } from "./losses";
import { LossRow } from "./Losses";
import { Population, Warnings } from "./Network";
import { decodeNetworkWarning } from "./networkWarnings";
import { decodeScheduleRun } from "./scheduleRun";
import { Explanation, LatestRound, Planned } from "./ScheduleTimeline";
import { decodeStationSchedule } from "./stationSchedule";

// Leaflet reads `window` when it is imported, and these run with no DOM; the
// map is not what is under test here.
vi.mock("./StationMap", () => ({ StationMap: () => null }));

const BADGE = 'class="badge badge-simulated"';
const go = () => undefined;

const proportion = { numerator: 41, denominator: 47, estimate: 0.87, interval: [0.79, 0.92] as [number, number] };
const budget = { allowed: 12.5, spent: 3, remaining: 9.5, exhausted: false };

const schedule = decodeStationSchedule({
  station_id: "st_1",
  simulated: true,
  start: "2026-11-02T10:00:00Z",
  end: "2026-11-02T16:00:00Z",
  entries: [{ assignment: assignmentBody("as_1"), max_elevation_deg: 61, element_set_epoch: "2026-10-31T10:04:00Z" }],
});

function badges(markup: string): number {
  return markup.split(BADGE).length - 1;
}

describe("the simulated badge", () => {
  it("marks the simulated population and not the measured one", () => {
    const simulated = renderToStaticMarkup(<Population population={{ simulated: true, passes: 47, capture: proportion, budget }} />);
    const measured = renderToStaticMarkup(<Population population={{ simulated: false, passes: 47, capture: proportion, budget }} />);
    expect(badges(simulated)).toBe(1);
    expect(badges(measured)).toBe(0);
    expect(simulated).toContain("0.87 [0.79, 0.92], 41 of 47 passes");
  });

  it("marks a simulated station's warning, loss, schedule and decision", () => {
    const warning = renderToStaticMarkup(<Warnings warnings={[decodeNetworkWarning(warningBody, "w")]} go={go} />);
    const loss = renderToStaticMarkup(
      <table>
        <tbody>
          <LossRow loss={decodeLoss(lossBody, "loss")} go={go} />
        </tbody>
      </table>,
    );
    const planned = renderToStaticMarkup(<Planned schedule={schedule} />);
    const [entry] = schedule.entries;
    if (entry === undefined) {
      throw new Error("expected an entry");
    }
    const explained = renderToStaticMarkup(<Explanation entry={entry} />);
    for (const markup of [warning, loss, planned, explained]) {
      expect(badges(markup)).toBeGreaterThan(0);
    }
    expect(explained).toContain("orbit 2.0 days old");
    expect(explained).toContain("pass 8, worth 211.4");
  });

  it("marks a round that decided only for simulated stations", () => {
    const run = decodeScheduleRun(runBody);
    expect(badges(renderToStaticMarkup(<LatestRound run={run} />))).toBe(1);
    expect(badges(renderToStaticMarkup(<LatestRound run={{ ...run, measuredDecisions: 3 }} />))).toBe(0);
    expect(renderToStaticMarkup(<LatestRound run={run} />)).toContain("not measured live");
  });

  it("is absent from a measured model run, whose label is shown instead", () => {
    const read = decodeInsights(insightsBody);
    const markup = renderToStaticMarkup(<ModelPanel read={read} />);
    expect(badges(markup)).toBe(0);
    expect(markup).toContain("not by Meridian");
    expect(markup).toContain("not computable. needs a learned station profile");
    const simulated = decodeInsights({ ...insightsBody, simulated: true });
    expect(badges(renderToStaticMarkup(<ModelPanel read={simulated} />))).toBe(1);
  });
});

describe("an absence", () => {
  it("is said in words, never drawn as a zero", () => {
    const empty = renderToStaticMarkup(<Population population={{ simulated: false, passes: 0, capture: proportion, budget }} />);
    expect(empty).toContain("No pass has settled in the window.");
    expect(empty).not.toContain("0.87");
    expect(renderToStaticMarkup(<Warnings warnings={[]} go={go} />)).toContain("No receive-chain warning is open.");
    expect(renderToStaticMarkup(<LatestRound run={null} />)).toContain("No scheduling round has decided anything yet.");
    expect(
      renderToStaticMarkup(<ModelPanel read={{ notYetComputed: "No run yet." }} />),
    ).toContain("Not yet computed. No run yet.");
  });
});

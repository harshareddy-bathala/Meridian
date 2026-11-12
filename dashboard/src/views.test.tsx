// Every view that draws a figure from simulated rows marks it (CLAUDE.md rule
// 5), and one drawn from measured rows does not. Rendered to static markup,
// which runs no effect and fetches nothing: each view's presentational parts
// are given simulated data directly.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { decodeAggregates } from "./aggregates";
import { decodeAreas } from "./areas";
import { ChangeLine, Sources } from "./Areas";
import { decodeEvent } from "./events";
import { Feed } from "./Feed";
import {
  aggregatesBody,
  areasBody,
  assignmentBody,
  eventBody,
  insightsBody,
  lossBody,
  resultBody,
  runBody,
  tracksBody,
  warningBody,
  weeklyBody,
} from "./fixtures.test-data";
import { decodeInsights } from "./insights";
import { ModelPanel } from "./ModelSummary";
import { decodeLoss } from "./losses";
import { LossRow } from "./Losses";
import { Warnings } from "./Live";
import { AreaReports, WeekText } from "./Reports";
import { decodeWeekly } from "./reports";
import { Population, ResultRow } from "./Results";
import { decodeResult } from "./results";
import { TodayFigures, Week } from "./LiveFigures";
import { Orbits, Overhead } from "./LiveNow";
import { decodeAssignment } from "./schedule";
import { decodeTracks } from "./tracks";
import { decodeNetworkWarning } from "./networkWarnings";
import { decodeScheduleRun } from "./scheduleRun";
import { Explanation, LatestRound, Planned } from "./ScheduleTimeline";
import { decodeStationSchedule } from "./stationSchedule";

// Leaflet reads `window` when it is imported, and these run with no DOM; the
// map is not what is under test here.
vi.mock("./StationMap", () => ({ StationMap: () => null }));
vi.mock("./AreaMap", () => ({ AreaMap: () => null }));

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

  it("marks the weekly report's simulated section, and only that one", () => {
    const markup = renderToStaticMarkup(<WeekText week={decodeWeekly(weeklyBody)} />);
    expect(badges(markup)).toBe(1);
    expect(markup).toContain("SIMULATED STATIONS — not real receptions.");
    const measured = weeklyBody.body.split("\n\nSIMULATED")[0] ?? "";
    const plain = decodeWeekly({ ...weeklyBody, body: measured, includes_simulated: false });
    expect(badges(renderToStaticMarkup(<WeekText week={plain} />))).toBe(0);
  });

  it("is absent from the area reports, which hold no station's figure", () => {
    const markup = renderToStaticMarkup(<AreaReports read={decodeAreas(areasBody)} />);
    expect(badges(markup)).toBe(0);
    expect(markup).toContain('href="#/areas/1"');
    expect(markup).toContain("Vegetation (NDVI): 0.4 for");
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

describe("the live page", () => {
  const aggregates = decodeAggregates(aggregatesBody);
  const names = new Map([["st_1", "Rooftop"]]);

  it("marks today's and the week's simulated figures, and not the measured ones", () => {
    const simulated = renderToStaticMarkup(<TodayFigures population={aggregates.simulated} marginS={86_400} />);
    expect(badges(simulated)).toBe(1);
    expect(simulated).toContain("40, 22 closed so far");
    expect(simulated).toContain("once a day has passed");
    expect(badges(renderToStaticMarkup(<Week population={aggregates.simulated} />))).toBe(1);
    expect(badges(renderToStaticMarkup(<TodayFigures population={aggregates.measured} marginS={86_400} />))).toBe(0);
  });

  it("marks a simulated event and pass, and not a measured event", () => {
    const simulated = renderToStaticMarkup(
      <Feed events={[decodeEvent(eventBody("pass_decoded"), "e")]} names={names} go={go} />,
    );
    expect(badges(simulated)).toBe(1);
    expect(simulated).toContain("Rooftop");
    const measured = renderToStaticMarkup(
      <Feed events={[decodeEvent(eventBody("station_joined", false), "e")]} names={names} go={go} />,
    );
    expect(badges(measured)).toBe(0);
    const pass = decodeAssignment(assignmentBody("as_1"), "a");
    const now = new Date(Date.parse(pass.startAt) + 60_000);
    expect(badges(renderToStaticMarkup(<Overhead passes={[pass]} now={now} names={names} />))).toBe(1);
  });

  it("draws an unsettled day hollow, and says so in words beside it", () => {
    const week = renderToStaticMarkup(<Week population={aggregates.simulated} />);
    expect(week.split("sparkline-unsettled").length - 1).toBe(4);
    expect(week).toContain("not settled yet");
    expect(week).toContain("not yet");
  });

  it("says every absence in words", () => {
    expect(renderToStaticMarkup(<TodayFigures population={aggregates.measured} marginS={86_400} />)).toContain(
      "No pass is scheduled to close today.",
    );
    expect(renderToStaticMarkup(<Week population={aggregates.measured} />)).toContain(
      "No pass was classified this week.",
    );
    expect(renderToStaticMarkup(<Feed events={[]} names={names} go={go} />)).toContain(
      "Nothing was recorded in the last week.",
    );
    const quiet = renderToStaticMarkup(<Overhead passes={[]} now={new Date()} names={names} />);
    expect(quiet).toContain("No scheduled pass is under way.");
    expect(quiet).toContain("No later pass is scheduled yet.");
    expect(renderToStaticMarkup(<Orbits tracks={{ at: "", satellites: [], omitted: [] }} />)).toContain(
      "No satellite is tracked yet.",
    );
    expect(renderToStaticMarkup(<Orbits tracks={decodeTracks(tracksBody)} />)).toContain("no orbit is held for it");
  });
});

describe("the results page", () => {
  const row = (simulated: boolean) =>
    renderToStaticMarkup(
      <table>
        <tbody>
          <ResultRow result={decodeResult(resultBody(1, "station_unavailable", "lost", simulated), "r")} go={go} />
        </tbody>
      </table>,
    );

  it("marks a simulated pass, and not a measured one", () => {
    expect(badges(row(true))).toBe(1);
    expect(badges(row(false))).toBe(0);
  });

  it("says what the pass did, and the cause it was diagnosed with", () => {
    expect(row(true)).toContain("Station unavailable");
    expect(row(true)).toContain("lost: spends the loss budget");
    expect(row(true)).toContain("something blocks that part of the sky");
  });
});

describe("the areas pages", () => {
  const read = decodeAreas(areasBody);
  if (!("summary" in read)) throw new Error("expected a summary");
  const { summary } = read;

  it("give a change with its interval and periods, and badge nothing: no value is a station's", () => {
    const [change] = summary.changes;
    if (change === undefined) throw new Error("expected a change");
    const markup = renderToStaticMarkup(
      <ul>
        <ChangeLine change={change} label="Bengaluru urban" />
      </ul>,
    );
    expect(markup).toContain("Bengaluru urban: Vegetation (NDVI)");
    expect(markup).toContain("Within its threshold");
    expect(markup).toContain("-0.0244 [-0.05, 0.03]");
    expect(badges(markup)).toBe(0);
  });

  it("credit every source with its licence and terms, and the report they came from", () => {
    const markup = renderToStaticMarkup(<Sources summary={summary} />);
    expect(markup).toContain("NASA open data");
    expect(markup).toContain('href="https://example.org/terms"');
    expect(markup).toContain("aaaaaaaaaaaa");
  });
});

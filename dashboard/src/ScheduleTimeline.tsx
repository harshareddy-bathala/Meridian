import { useState } from "react";

import { formatDay, formatWindow } from "./format";
import type { Route } from "./route";
import { describeValue, type Explanation as Explanation_ } from "./schedule";
import { fetchLatestRun, type ScheduleRun } from "./scheduleRun";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass } from "./snapshot";
import {
  describeElementSetAge,
  fetchStationSchedule,
  SCHEDULE_HOURS,
  type ScheduleEntry,
  type StationSchedule,
} from "./stationSchedule";
import type { Station } from "./stations";
import { usePolled } from "./usePolled";
import { useStations } from "./useStations";

// One station's next hours, and why each pass was chosen or passed over: the
// scheduler's own record of what it weighed (D-170), the pass's predicted
// yield, and how old the orbit behind it is. Windows are whole minutes (D-093).

type Go = (route: Route) => void;

const WIDTH = 1000;
const ROW = 28;

function Picker({ stations, chosen, go }: { stations: Station[]; chosen: string | null; go: Go }) {
  return (
    <label className="picker">
      Station{" "}
      <select
        value={chosen ?? ""}
        onChange={(event) => {
          go({ view: "schedule", stationId: event.target.value === "" ? null : event.target.value });
        }}
      >
        <option value="">Choose a station</option>
        {stations.map((one) => (
          <option key={one.stationId} value={one.stationId}>
            {one.name} ({one.stationId})
          </option>
        ))}
      </select>
    </label>
  );
}

interface TimelineProps {
  schedule: StationSchedule;
  selected: string | null;
  onSelect: (assignmentId: string) => void;
}

function Timeline({ schedule, selected, onSelect }: TimelineProps) {
  const start = Date.parse(schedule.start);
  const span = Date.parse(schedule.end) - start;
  const x = (iso: string) => Math.max(0, Math.min(WIDTH, ((Date.parse(iso) - start) / span) * WIDTH));
  const height = Math.max(1, schedule.entries.length) * ROW + ROW;
  return (
    <svg className="timeline" viewBox={`0 0 ${String(WIDTH)} ${String(height)}`} role="img"
      aria-label={`The next ${String(SCHEDULE_HOURS)} hours, one row per pass`}>
      {Array.from({ length: SCHEDULE_HOURS + 1 }, (_, hour) => (
        <line key={hour} className="timeline-hour" x1={(hour / SCHEDULE_HOURS) * WIDTH} x2={(hour / SCHEDULE_HOURS) * WIDTH}
          y1={0} y2={height} />
      ))}
      {schedule.entries.map((entry, row) => {
        const { assignment } = entry;
        const left = x(assignment.startAt);
        return (
          <g key={assignment.assignmentId} className={`timeline-pass timeline-${assignment.decision}${
            selected === assignment.assignmentId ? " selected" : ""}`}
            onClick={() => { onSelect(assignment.assignmentId); }}>
            <rect x={left} y={row * ROW + 4} width={Math.max(4, x(assignment.endAt) - left)} height={ROW - 8} />
            <text x={left + 6} y={row * ROW + ROW / 2 + 4}>
              {assignment.satelliteId} · {String(entry.maxElevationDeg)}° · {assignment.decision}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function EntryList({ schedule, selected, onSelect }: TimelineProps) {
  return (
    <ol className="entries">
      {schedule.entries.map(({ assignment }) => (
        <li key={assignment.assignmentId}>
          <button
            type="button"
            className="link"
            aria-pressed={selected === assignment.assignmentId}
            onClick={() => {
              onSelect(assignment.assignmentId);
            }}
          >
            {formatWindow(assignment.startAt, assignment.endAt)} {assignment.satelliteId}
          </button>{" "}
          <span className={`badge badge-${assignment.decision}`}>{assignment.decision}</span>
        </li>
      ))}
    </ol>
  );
}

/** What took a skipped pass's slot, or the best a chosen one displaced. */
function displaced(why: Explanation_): string {
  const rule = why.rule === null ? "" : ` (${why.rule === "overlap" ? "the windows overlap" : "the delivery cap"})`;
  if (why.alternative === null) {
    return `nothing: no pass competed for its time${rule}`;
  }
  const worth = why.alternative.value === null ? "an earlier commitment" : why.alternative.value.toFixed(1);
  return `pass ${String(why.alternative.passId)}, worth ${worth}${rule}`;
}

function Reasons({ why, decision }: { why: Explanation_; decision: string }) {
  const weighed = why.weighedAgainst.length;
  return (
    <>
      <dt>Value</dt>
      <dd className="mono">{describeValue(why)}</dd>
      <dt>{decision === "skipped" ? "Lost its slot to" : "Displaced"}</dt>
      <dd>{displaced(why)}</dd>
      <dt>Weighed against</dt>
      <dd>
        {weighed} {weighed === 1 ? "pass" : "passes"}
      </dd>
    </>
  );
}

function Explanation({ entry }: { entry: ScheduleEntry }) {
  const { assignment } = entry;
  const why = assignment.explanation;
  return (
    <aside className="explanation" aria-label="Why this pass">
      <h3>
        {assignment.satelliteId}, {formatWindow(assignment.startAt, assignment.endAt)}{" "}
        <span className={`badge badge-${assignment.decision}`}>{assignment.decision}</span>{" "}
        {assignment.simulated && <SimBadge />}
      </h3>
      <p>{assignment.reason}</p>
      <dl>
        <dt>Peak elevation</dt>
        <dd>{entry.maxElevationDeg}°</dd>
        <dt>Element set</dt>
        <dd>{describeElementSetAge(entry)}</dd>
        {why !== null && <Reasons why={why} decision={assignment.decision} />}
      </dl>
      {why === null && <p className="dim">This decision was made before decisions explained themselves.</p>}
    </aside>
  );
}

function LatestRound({ run }: { run: ScheduleRun | null }) {
  if (run === null) {
    return <p className="dim">No scheduling round has decided anything yet.</p>;
  }
  return (
    <div className="figure">
      <h3>
        Latest round {run.measuredDecisions === 0 && run.simulatedDecisions > 0 && <SimBadge />}
      </h3>
      <p>
        Configuration {run.configuration}, {formatDay(run.decidedAt)}: {run.scheduled} scheduled and {run.skipped}{" "}
        skipped of {run.candidates} candidates, solved {run.solverStatus.replace("_", " ")} in{" "}
        {run.runtimeS.toFixed(1)} s.
      </p>
      <p className="dim">Gain over a baseline: not measured live. {run.baselineGain}</p>
    </div>
  );
}

function Planned({ schedule }: { schedule: StationSchedule }) {
  const [selected, setSelected] = useState<string | null>(null);
  const entry = schedule.entries.find((one) => one.assignment.assignmentId === selected) ?? null;
  return (
    <>
      <p>
        The next {SCHEDULE_HOURS} hours of {schedule.stationId} {schedule.simulated && <SimBadge />}:{" "}
        {schedule.entries.length} passes decided.
      </p>
      <Timeline schedule={schedule} selected={selected} onSelect={setSelected} />
      <EntryList schedule={schedule} selected={selected} onSelect={setSelected} />
      {entry === null ? (
        <p className="dim">Choose a pass to see why it was decided as it was.</p>
      ) : (
        <Explanation entry={entry} />
      )}
    </>
  );
}

export function ScheduleView({ stationId, go }: { stationId: string | null; go: Go }) {
  const directory = useStations();
  const schedule = usePolled(`schedule:${stationId ?? ""}`, (fetcher, signal) =>
    stationId === null ? Promise.resolve(null) : fetchStationSchedule(fetcher, stationId, signal),
  );
  const run = usePolled("latest-run", fetchLatestRun);
  const snapshot = oldest(schedule.snapshot, run.snapshot);
  return (
    <section aria-labelledby="schedule-heading" className={snapshotClass("view", snapshot)}>
      <h2 id="schedule-heading">Schedule</h2>
      <SnapshotNote snapshot={snapshot} what="This view shows" />
      {directory.stations !== null && <Picker stations={directory.stations} chosen={stationId} go={go} />}
      {schedule.error !== null && <p role="alert">{schedule.error}</p>}
      {schedule.value !== null && <Planned key={schedule.value.stationId} schedule={schedule.value} />}
      {run.fetchedAt !== null && <LatestRound run={run.value} />}
    </section>
  );
}

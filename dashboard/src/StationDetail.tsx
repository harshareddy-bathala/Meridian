import { formatAge, formatDay, formatFrequency, formatWindow } from "./format";
import { HorizonPlot } from "./HorizonPlot";
import type { StationProfiles } from "./profiles";
import { describeSignal, outcomeLabel, type Reception } from "./receptions";
import { describeCapture, type StationCapture } from "./reliability";
import type { StationUptime } from "./uptime";
import { UptimeStrip } from "./UptimeStrip";
import { describeValue, type Assignment, type LatestHeartbeat } from "./schedule";
import type { Station } from "./stations";
import { useStationCapture } from "./useStationCapture";
import { useStationDetail } from "./useStationDetail";

function ListeningState({ heartbeat }: { heartbeat: LatestHeartbeat | null | undefined }) {
  if (heartbeat === undefined) {
    return <p className="dim">Loading the latest heartbeat…</p>;
  }
  if (heartbeat === null) {
    return <p>This station has never sent a heartbeat.</p>;
  }
  const age = formatAge(heartbeat.receivedAt, new Date());
  const { listening } = heartbeat;
  // "Not listening" is a report, not an absence of one (MSP §4.2): it is what
  // lets a quiet pass be told apart from a missed one.
  return listening === null ? (
    <p>
      Not listening — reported <strong>{heartbeat.state}</strong> {age}.
    </p>
  ) : (
    <p>
      Listening to <strong>{listening.satelliteId}</strong> on{" "}
      {formatFrequency(listening.centreFreqHz)} ({listening.mode}) for{" "}
      <span className="mono">{listening.assignmentId}</span>, reported {age}.
    </p>
  );
}

function AssignmentRow({ assignment, showStation }: { assignment: Assignment; showStation: boolean }) {
  const { explanation } = assignment;
  return (
    <tr>
      <td className="mono">{assignment.satelliteId}</td>
      {showStation && <td className="mono">{assignment.stationId}</td>}
      <td>{formatWindow(assignment.startAt, assignment.endAt)}</td>
      <td>
        <span className={`badge badge-${assignment.decision}`}>{assignment.decision}</span>
      </td>
      <td>
        {assignment.reason}
        {assignment.conflictsWith !== null && (
          <div className="dim mono">lost to {assignment.conflictsWith}</div>
        )}
        {explanation !== null && (
          <div className="dim">
            {describeValue(explanation)}
            {assignment.decision === "scheduled" && explanation.alternative !== null && (
              <>; displaced pass {explanation.alternative.passId}</>
            )}
            {explanation.runStatus === "fallback" && <>; the solver fell back to greedy</>}
          </div>
        )}
      </td>
      <td>{assignment.state === null ? "—" : assignment.state.replace("_", " ")}</td>
    </tr>
  );
}

function Assignments({ assignments, showStation }: { assignments: Assignment[]; showStation: boolean }) {
  if (assignments.length === 0) {
    return <p>No assignment is coming up.</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Satellite</th>
            {showStation && <th scope="col">Station</th>}
            <th scope="col">Window</th>
            <th scope="col">Decision</th>
            <th scope="col">Reason</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {assignments.map((assignment) => (
            <AssignmentRow key={assignment.assignmentId} assignment={assignment} showStation={showStation} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SimulatedBadge() {
  return (
    <span className="badge badge-simulated" title="Reported by a virtual station run by the simulator">
      simulated
    </span>
  );
}

function Receptions({ receptions }: { receptions: Reception[] }) {
  if (receptions.length === 0) {
    return <p>This station has reported no reception yet.</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Satellite</th>
            <th scope="col">Window</th>
            <th scope="col">Outcome</th>
            <th scope="col">Signal</th>
            <th scope="col">Products</th>
          </tr>
        </thead>
        <tbody>
          {receptions.map((reception) => (
            <tr key={reception.observationId}>
              <td className="mono">{reception.satelliteId}</td>
              <td>{formatWindow(reception.startedAt, reception.endedAt)}</td>
              <td>
                <span className={`badge badge-outcome badge-${reception.outcome}`}>{outcomeLabel(reception.outcome)}</span>
                {reception.simulated && <SimulatedBadge />}
                {reception.revision > 1 && <div className="dim">corrected, revision {reception.revision}</div>}
              </td>
              <td>{describeSignal(reception)}</td>
              <td>{reception.products === 0 ? "—" : String(reception.products)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Capture({ capture }: { capture: StationCapture | null }) {
  if (capture === null) {
    return <p>No pass of this station has settled yet, so there is no capture rate.</p>;
  }
  return (
    <p>
      Capture rate <strong>{describeCapture(capture.capture)}</strong> (95% interval), for passes whose windows
      closed between {formatDay(capture.windowStart)} and {formatDay(capture.windowEnd)}.{" "}
      {capture.simulated && <SimulatedBadge />}
    </p>
  );
}

interface ResultsProps {
  receptions: Reception[] | null | undefined;
  capture: StationCapture | null | undefined;
}

function Results({ receptions, capture }: ResultsProps) {
  return (
    <>
      <h3>Results</h3>
      {capture !== undefined && <Capture capture={capture} />}
      {receptions === undefined && <p className="dim">Loading receptions…</p>}
      {receptions === null && <p className="dim">The receptions could not be read.</p>}
      {Array.isArray(receptions) && <Receptions receptions={receptions} />}
    </>
  );
}

function StationSky({ profiles, uptime }: { profiles: StationProfiles | null; uptime: StationUptime | null }) {
  return (
    <>
      {uptime !== null && (
        <>
          <h3>Uptime</h3>
          <UptimeStrip uptime={uptime} />
        </>
      )}
      {profiles !== null && (
        <>
          <h3>Horizon</h3>
          <HorizonPlot profiles={profiles} />
        </>
      )}
    </>
  );
}

function UpcomingAssignments({
  assignments,
  error,
  showStation,
}: {
  assignments: Assignment[] | null;
  error: string | null;
  showStation: boolean;
}) {
  if (assignments === null) {
    return error === null ? <p className="dim">Loading assignments…</p> : null;
  }
  return <Assignments assignments={assignments} showStation={showStation} />;
}

interface DetailProps {
  station: Station | null;
  onClear: () => void;
}

export function StationDetail({ station, onClear }: DetailProps) {
  const stationId = station?.stationId ?? null;
  const { heartbeat, assignments, profiles, uptime, receptions, error } = useStationDetail(stationId);
  const capture = useStationCapture(stationId);
  return (
    <section aria-labelledby="detail-heading" className="station-detail">
      <h2 id="detail-heading">
        {station === null ? "Upcoming assignments, whole network" : station.name}
      </h2>
      {station !== null && (
        <>
          <button type="button" className="link" onClick={onClear}>
            Show the whole network
          </button>
          <ListeningState heartbeat={heartbeat} />
        </>
      )}
      {error !== null && <p role="alert">Could not refresh: {error}</p>}
      <UpcomingAssignments assignments={assignments} error={error} showStation={station === null} />
      {station !== null && <Results receptions={receptions} capture={capture} />}
      <StationSky profiles={profiles} uptime={uptime} />
    </section>
  );
}

import { formatAge, formatFrequency, formatWindow } from "./format";
import type { Assignment, LatestHeartbeat } from "./schedule";
import type { Station } from "./stations";
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
      </td>
      <td>{assignment.state.replace("_", " ")}</td>
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

interface DetailProps {
  station: Station | null;
  onClear: () => void;
}

export function StationDetail({ station, onClear }: DetailProps) {
  const { heartbeat, assignments, error } = useStationDetail(station?.stationId ?? null);
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
      {assignments === null ? (
        error === null && <p className="dim">Loading assignments…</p>
      ) : (
        <Assignments assignments={assignments} showStation={station === null} />
      )}
    </section>
  );
}

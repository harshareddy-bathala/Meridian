// The live page's first screen: the time, what is overhead, the next pass, the
// orbits it is all predicted from, and the geomagnetic index. Presentational:
// the view hands each one its data.

import { describeSample, type Conditions } from "./conditions";
import { formatCountdown, formatUtcClock } from "./format";
import { nextPass, passesUnderWay, type Assignment } from "./schedule";
import { SimBadge } from "./SimBadge";
import { STALE_ORBIT_DAYS } from "./stationSchedule";
import { describeOmission, describeOrbitAge, type SatelliteTracks } from "./tracks";

export function Clock({ now }: { now: Date }) {
  return (
    <p className="clock">
      <time dateTime={now.toISOString()}>{formatUtcClock(now)}</time>
    </p>
  );
}

interface PassesProps {
  passes: Assignment[];
  now: Date;
  /** A station's name by its id, so a pass names the station a reader knows. */
  names: Map<string, string>;
}

function stationName(names: Map<string, string>, stationId: string): string {
  return names.get(stationId) ?? stationId;
}

export function Overhead({ passes, now, names }: PassesProps) {
  const nowMs = now.getTime();
  const under = passesUnderWay(passes, nowMs);
  const next = nextPass(passes, nowMs);
  return (
    <div className="overhead">
      {under.length === 0 ? (
        <p>No scheduled pass is under way.</p>
      ) : (
        <ul className="under-way">
          {under.map((one) => (
            <li key={one.assignmentId}>
              <span className="mono">{one.satelliteId}</span> over {stationName(names, one.stationId)}, ends{" "}
              {formatCountdown(Date.parse(one.endAt) - nowMs)} {one.simulated && <SimBadge />}
            </li>
          ))}
        </ul>
      )}
      {next === null ? (
        <p className="dim">No later pass is scheduled yet.</p>
      ) : (
        <p>
          Next: <span className="mono">{next.satelliteId}</span> over {stationName(names, next.stationId)},{" "}
          <strong>{formatCountdown(Date.parse(next.startAt) - nowMs)}</strong> {next.simulated && <SimBadge />}
        </p>
      )}
    </div>
  );
}

export function Orbits({ tracks }: { tracks: SatelliteTracks }) {
  if (tracks.satellites.length === 0 && tracks.omitted.length === 0) {
    return <p className="dim">No satellite is tracked yet.</p>;
  }
  return (
    <ul className="orbits">
      {tracks.satellites.map((one) => (
        <li key={one.satelliteId}>
          {one.name}: {describeOrbitAge(one.elementSetAgeS, STALE_ORBIT_DAYS)}
          {!one.isActive && ", believed silent"}
        </li>
      ))}
      {tracks.omitted.map((one) => (
        <li key={one.satelliteId} className="dim">
          {describeOmission(one)}
        </li>
      ))}
    </ul>
  );
}

export function Kp({ conditions }: { conditions: Conditions }) {
  const { kp } = conditions;
  if ("notMeasured" in kp) {
    return <p className="dim">Geomagnetic index: not measured. {kp.notMeasured}</p>;
  }
  return (
    <p>
      {describeSample(kp, "Kp")}, published by {kp.source.name} ({kp.source.licence}).
    </p>
  );
}

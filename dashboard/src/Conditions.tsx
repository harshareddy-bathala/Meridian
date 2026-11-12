import { describeSample, type Conditions, type Sample } from "./conditions";
import { formatDay } from "./format";

// The world outside the stations: the geomagnetic index and the cloud over them.
// Each is published by others and credited to them (D-134); none is Meridian's
// own measurement, and the scheduler never reads them from here (D-224).

function Credit({ sample }: { sample: Sample }) {
  return (
    <span className="dim">
      {" "}
      — {sample.source.name}, published {formatDay(sample.publishedAt)}, {sample.source.licence} (
      <a href={sample.source.termsUrl} rel="noreferrer">
        terms
      </a>
      )
    </span>
  );
}

export function ConditionsFigure({ conditions }: { conditions: Conditions }) {
  return (
    <div className="figure">
      <h3>Conditions</h3>
      <p>
        {"notMeasured" in conditions.kp ? (
          <span className="dim">Kp: not measured. {conditions.kp.notMeasured}</span>
        ) : (
          <>
            {describeSample(conditions.kp, "Kp")}
            <Credit sample={conditions.kp} />
          </>
        )}
      </p>
      {conditions.cloudCover.length === 0 ? (
        <p className="dim">No cloud forecast covers this hour.</p>
      ) : (
        <ul>
          {conditions.cloudCover.map((one) => (
            <li key={`${String(one.latDeg)},${String(one.lonDeg)}`}>
              {describeSample(one, `Cloud at ${String(one.latDeg)}°, ${String(one.lonDeg)}°`)}
              <Credit sample={one} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

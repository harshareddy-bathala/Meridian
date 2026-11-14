import { useEffect, useRef, useState, type SyntheticEvent } from "react";

import { formatFrequency, formatMinute, formatWindow } from "./format";
import {
  compassPoint,
  describeOmission,
  expectedDecodes,
  fetchPlan,
  formatPlace,
  passesBySatellite,
  type Place,
  type Plan,
  type PlanPass,
  type PlanSatellite,
  type PlanYield,
  placeProblem,
  roundPlace,
} from "./plan";
import { STALE_ORBIT_DAYS } from "./stationSchedule";
import { describeOrbitAge } from "./tracks";
import { JOIN_URL } from "./WaysIn";

// The station planner (D-370), for someone deciding whether a station is worth
// building: a place in, the next two days of passes over it out, each with its
// peak and how likely configuration A's model says it is to decode. The place
// is rounded to a tenth of a degree before it is sent and is kept nowhere: not
// in the fragment, not by the front, not in the platform's log.

type Load =
  | { kind: "idle" }
  | { kind: "loading"; place: Place }
  | { kind: "loaded"; plan: Plan }
  | { kind: "failed"; reason: string };

function percent(value: number): string {
  return `${String(Math.round(value * 100))}%`;
}

function YieldCell({ predicted }: { predicted: PlanYield }) {
  return "probability" in predicted ? (
    <>{percent(predicted.probability)}</>
  ) : (
    <span className="dim">not measured</span>
  );
}

function Summary({ plan }: { plan: Plan }) {
  const expected = expectedDecodes(plan.passes);
  const highest = plan.passes.reduce<PlanPass | null>(
    (best, one) =>
      best === null || one.maxElevationDeg > best.maxElevationDeg ? one : best,
    null,
  );
  return (
    <dl className="figures">
      <div className="figure">
        <dt>Passes in the next 48 hours</dt>
        <dd>
          {plan.passes.length}{" "}
          <span className="dim">
            from {plan.satellites.length}{" "}
            {plan.satellites.length === 1 ? "satellite" : "satellites"}, above{" "}
            {plan.minElevationDeg}°
          </span>
        </dd>
      </div>
      <div className="figure">
        <dt>Decodes to expect</dt>
        <dd>
          {expected === null ? (
            <span className="dim">not measured</span>
          ) : (
            <>
              about {expected.toFixed(1)}{" "}
              <span className="dim">by configuration A</span>
            </>
          )}
        </dd>
      </div>
      <div className="figure">
        <dt>Highest pass</dt>
        <dd>
          {highest === null ? (
            <span className="dim">none</span>
          ) : (
            <>
              {highest.maxElevationDeg}°{" "}
              <span className="dim">
                at {formatMinute(highest.maxElevationAt)}
              </span>
            </>
          )}
        </dd>
      </div>
    </dl>
  );
}

function YieldNote({ plan }: { plan: Plan }) {
  const model = plan.yieldModel;
  if ("notYetComputed" in model) {
    return (
      <p className="dim">
        {model.notYetComputed} The passes and their peaks are still computed; a
        pass's peak is the strongest single sign of whether it decodes.
      </p>
    );
  }
  const whose =
    model.population === "own" ? "Meridian's own stations" : "a public archive";
  return (
    <p className="dim">
      Each yield is configuration A's: a model that reads only how high a pass
      climbs, fitted on receptions by {whose} up to {formatMinute(model.asOf)}{" "}
      (model <span className="mono">{model.modelSha256.slice(0, 12)}</span>). It
      says how a typical station of the network would do with the pass. A roof's
      own horizon and interference are not in it: a station learns those from
      its own receptions once it joins.
    </p>
  );
}

function Satellites({ plan }: { plan: Plan }) {
  const grouped = passesBySatellite(plan);
  return (
    <div className="table-scroll">
      <table>
        <caption>
          Each satellite searched, what to tune to, and its passes over the
          place
        </caption>
        <thead>
          <tr>
            <th scope="col">Satellite</th>
            <th scope="col">Downlink</th>
            <th scope="col">Passes</th>
            <th scope="col">Decodes to expect</th>
            <th scope="col">Orbit</th>
          </tr>
        </thead>
        <tbody>
          {plan.satellites.map((one: PlanSatellite) => {
            const passes = grouped.get(one.satelliteId) ?? [];
            const expected = expectedDecodes(passes);
            return (
              <tr key={one.satelliteId}>
                <th scope="row">{one.name}</th>
                <td>
                  {one.downlinks.map((downlink) => (
                    <span
                      key={`${String(downlink.centreFreqHz)}-${downlink.mode}`}
                      className="downlink"
                    >
                      {formatFrequency(downlink.centreFreqHz)}{" "}
                      {downlink.mode.toUpperCase()}
                    </span>
                  ))}
                </td>
                <td>{passes.length}</td>
                <td>
                  {expected === null ? (
                    <span className="dim">not measured</span>
                  ) : (
                    expected.toFixed(1)
                  )}
                </td>
                <td>
                  {describeOrbitAge(one.elementSetAgeS, STALE_ORBIT_DAYS)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Passes({ plan }: { plan: Plan }) {
  const names = new Map(
    plan.satellites.map((one) => [one.satelliteId, one.name]),
  );
  if (plan.passes.length === 0) {
    return (
      <p className="dim">
        No satellite rises above {plan.minElevationDeg}° over this place in the
        next 48 hours.
      </p>
    );
  }
  return (
    <div className="table-scroll">
      <table>
        <caption>
          Every pass, in the order they rise. Windows are widened to whole
          minutes.
        </caption>
        <thead>
          <tr>
            <th scope="col">Satellite</th>
            <th scope="col">Window</th>
            <th scope="col">Peak</th>
            <th scope="col">Rises, sets</th>
            <th scope="col">Predicted yield</th>
          </tr>
        </thead>
        <tbody>
          {plan.passes.map((one) => (
            <tr key={`${one.satelliteId}-${one.aos}`}>
              <th scope="row">
                {names.get(one.satelliteId) ?? one.satelliteId}
              </th>
              <td>{formatWindow(one.aos, one.los)}</td>
              <td>{one.maxElevationDeg}°</td>
              <td>
                {compassPoint(one.aosAzimuthDeg)} to{" "}
                {compassPoint(one.losAzimuthDeg)}
              </td>
              <td>
                <YieldCell predicted={one.predictedYield} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Answer({ plan }: { plan: Plan }) {
  return (
    <>
      <h3>
        Over {formatPlace(plan.place)}, from {formatMinute(plan.at)}
      </h3>
      <Summary plan={plan} />
      <YieldNote plan={plan} />
      <Satellites plan={plan} />
      <Passes plan={plan} />
      {plan.omitted.length > 0 && (
        <ul className="dim">
          {plan.omitted.map((one) => (
            <li key={one.satelliteId}>{describeOmission(one)}</li>
          ))}
        </ul>
      )}
      <p>
        Worth building?{" "}
        <a href={JOIN_URL}>What a station needs, and how it joins</a>.
      </p>
    </>
  );
}

function usePlan(): [Load, (place: Place) => void] {
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      controller.current?.abort();
    },
    [],
  );
  const ask = (place: Place) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoad({ kind: "loading", place: roundPlace(place) });
    fetchPlan(fetch, place, current.signal)
      .then((plan) => {
        setLoad({ kind: "loaded", plan });
      })
      .catch((error: unknown) => {
        if (!current.signal.aborted) {
          setLoad({
            kind: "failed",
            reason: error instanceof Error ? error.message : String(error),
          });
        }
      });
  };
  return [load, ask];
}

function Coordinate({
  label,
  limit,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  limit: number;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        inputMode="decimal"
        step="0.1"
        min={String(-limit)}
        max={String(limit)}
        placeholder={placeholder}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        aria-describedby="plan-privacy"
      />
    </label>
  );
}

function PlaceForm({ ask }: { ask: (place: Place) => void }) {
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    const found = placeProblem(lat, lon);
    setProblem(found);
    if (found === null) {
      ask({ latDeg: Number(lat), lonDeg: Number(lon) });
    }
  };
  return (
    <>
      <form className="plan-form" onSubmit={submit} noValidate>
        <Coordinate
          label="Latitude, degrees north"
          limit={90}
          placeholder="12.9"
          value={lat}
          onChange={setLat}
        />
        <Coordinate
          label="Longitude, degrees east"
          limit={180}
          placeholder="77.6"
          value={lon}
          onChange={setLon}
        />
        <button type="submit">Show the passes</button>
      </form>
      <p id="plan-privacy" className="dim">
        The place is rounded to a tenth of a degree, about 11 km, before it
        leaves this page. Nothing about it is stored: not in this page's
        address, not on the platform, not in its log. South and west are
        negative.
      </p>
      {problem !== null && <p role="alert">{problem}</p>}
    </>
  );
}

export function PlanView() {
  const [load, ask] = usePlan();
  return (
    <section aria-labelledby="plan-heading" className="view">
      <h2 id="plan-heading">Plan a station</h2>
      <p>
        Give a place, and see what would pass over it in the next two days: how
        high each satellite would climb, and how likely each pass is to decode.
      </p>
      <PlaceForm ask={ask} />
      <div aria-live="polite">
        {load.kind === "loading" && (
          <p className="dim">
            Searching the next 48 hours over {formatPlace(load.place)}…
          </p>
        )}
        {load.kind === "failed" && (
          <p role="alert">The planner could not answer: {load.reason}</p>
        )}
        {load.kind === "loaded" && <Answer plan={load.plan} />}
      </div>
    </section>
  );
}

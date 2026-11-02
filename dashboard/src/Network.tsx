import { formatDay, formatFrequency, livenessLabel } from "./format";
import { fetchOpenWarnings, type NetworkWarning } from "./networkWarnings";
import {
  describeBudget,
  describeCapture,
  fetchNetworkReliability,
  type PopulationSummary,
} from "./reliability";
import type { Route } from "./route";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass } from "./snapshot";
import { StationMap } from "./StationMap";
import { LIVENESS, type Station } from "./stations";
import { usePolled } from "./usePolled";
import { useStations } from "./useStations";

// The network at a glance: where the stations are and which are heard, how
// many passes the network captured, how much of its loss budget is spent, and
// which receive chains are falling short. Simulated and measured figures are
// stated apart, each simulated one marked (rule 5).

type Go = (route: Route) => void;

function Fleet({ stations }: { stations: Station[] }) {
  const counts = LIVENESS.map((state) => ({
    state,
    n: stations.filter((one) => one.liveness === state).length,
  })).filter((one) => one.n > 0);
  const simulated = stations.filter((one) => one.simulated).length;
  return (
    <p>
      {stations.length} {stations.length === 1 ? "station" : "stations"}:{" "}
      {counts.map((one) => `${String(one.n)} ${livenessLabel(one.state).toLowerCase()}`).join(", ")}.{" "}
      {simulated > 0 && (
        <>
          {simulated} of them <SimBadge title="Virtual stations run by the simulator" />
        </>
      )}
    </p>
  );
}

export function Population({ population }: { population: PopulationSummary }) {
  const name = population.simulated ? "Simulated stations" : "Measured stations";
  return (
    <div className="figure">
      <h3>
        {name} {population.simulated && <SimBadge />}
      </h3>
      {population.passes === 0 ? (
        <p className="dim">No pass has settled in the window.</p>
      ) : (
        <dl>
          <dt>Capture rate</dt>
          <dd>{describeCapture(population.capture)}</dd>
          <dt>Loss budget</dt>
          <dd>{describeBudget(population.budget)}</dd>
        </dl>
      )}
    </div>
  );
}

export function Warnings({ warnings, go }: { warnings: NetworkWarning[]; go: Go }) {
  if (warnings.length === 0) {
    return <p className="dim">No receive-chain warning is open.</p>;
  }
  return (
    <ul className="warnings">
      {warnings.map((one) => (
        <li key={one.warningId}>
          <span className="badge badge-warning">Warning</span>{" "}
          <button
            type="button"
            className="link mono"
            onClick={() => {
              go({ view: "stations", stationId: one.stationId });
            }}
          >
            {one.stationId}
          </button>{" "}
          {one.band.toUpperCase()} {formatFrequency(one.freqMinHz)}: {one.shortfallDb.toFixed(1)} dB short
          since {formatDay(one.raisedAt)} {one.simulated && <SimBadge />}
        </li>
      ))}
    </ul>
  );
}

export function Network({ go }: { go: Go }) {
  const directory = useStations();
  const reliability = usePolled("reliability", fetchNetworkReliability);
  const warnings = usePolled("open-warnings", fetchOpenWarnings);
  const snapshot = oldest(directory.snapshot, oldest(reliability.snapshot, warnings.snapshot));
  const errors = [directory.error, reliability.error, warnings.error].filter((one) => one !== null);
  return (
    <section aria-labelledby="network-heading" className={snapshotClass("view", snapshot)}>
      <h2 id="network-heading">Network</h2>
      <SnapshotNote snapshot={snapshot} what="This view shows" />
      {errors.map((one) => (
        <p role="alert" key={one}>
          {one}
        </p>
      ))}
      {directory.stations !== null && (
        <>
          <Fleet stations={directory.stations} />
          <StationMap
            stations={directory.stations}
            onSelect={(stationId) => {
              go({ view: "stations", stationId });
            }}
          />
        </>
      )}
      <h3>Reliability</h3>
      {reliability.value === null ? (
        <p className="dim">Loading the reliability figures…</p>
      ) : (
        <div className="figures">
          <Population population={reliability.value.measured} />
          <Population population={reliability.value.simulated} />
          <p className="dim">
            Over {formatDay(reliability.value.windowStart)} to {formatDay(reliability.value.windowEnd)}. A pass
            counts once its settle margin has passed, so the newest passes are not in it yet.
          </p>
        </div>
      )}
      <h3>Receive-chain warnings</h3>
      {warnings.value !== null && <Warnings warnings={warnings.value} go={go} />}
    </section>
  );
}

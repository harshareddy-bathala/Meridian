import { fetchAggregates } from "./aggregates";
import { fetchConditions } from "./conditions";
import { fetchEvents } from "./events";
import { Feed } from "./Feed";
import { formatDay, formatFrequency, livenessLabel } from "./format";
import { TodayFigures, Week } from "./LiveFigures";
import { Clock, Kp, Orbits, Overhead } from "./LiveNow";
import { fetchOpenWarnings, type NetworkWarning } from "./networkWarnings";
import type { Route } from "./route";
import { fetchNetworkPasses } from "./schedule";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass, type Snapshot } from "./snapshot";
import { StationMap } from "./StationMap";
import { LIVENESS, type Station } from "./stations";
import { fetchTracks, type SatelliteTrack } from "./tracks";
import { useNow } from "./useNow";
import { usePolled } from "./usePolled";
import { useStations } from "./useStations";

// What is happening now, for a reader who owns no station: the time, what is
// overhead and where every satellite goes next, the next pass, today's passes
// and the week's, the stations and the orbits they are scheduled from, the
// geomagnetic index, and what just happened. No images (Stage 37). Every
// settled pass, and the thirty days' figures, are the results page's (D-365).
// Measured and simulated figures are stated apart, each simulated one marked
// (rule 5), and every absence is a sentence (D-086).

/** One empty list, so the map's effects do not see a new one every second. */
const EMPTY_TRACKS: SatelliteTrack[] = [];

/** Tracks are sampled once a minute on the platform (D-361). */
const TRACKS_REFRESH_MS = 60_000;

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

function useLive() {
  const reads = {
    directory: useStations(),
    tracks: usePolled("tracks", fetchTracks, TRACKS_REFRESH_MS),
    passes: usePolled("network-passes", fetchNetworkPasses),
    aggregates: usePolled("aggregates", fetchAggregates),
    events: usePolled("events", fetchEvents),
    conditions: usePolled("conditions", fetchConditions),
    warnings: usePolled("open-warnings", fetchOpenWarnings),
  };
  const all = Object.values(reads);
  const snapshot = all.reduce<Snapshot | null>((held, one) => oldest(held, one.snapshot), null);
  const errors = all.map((one) => one.error).filter((one) => one !== null);
  const names = new Map((reads.directory.stations ?? []).map((one) => [one.stationId, one.name]));
  return { ...reads, snapshot, errors, names };
}

type LiveReads = ReturnType<typeof useLive>;

function NowSection({ live, now, go }: { live: LiveReads; now: Date; go: Go }) {
  const stations = live.directory.stations;
  return (
    <>
      <Clock now={now} />
      {live.passes.value !== null && <Overhead passes={live.passes.value} now={now} names={live.names} />}
      {stations !== null && (
        <StationMap
          stations={stations}
          onSelect={(stationId) => {
            go({ view: "stations", stationId });
          }}
          // Always given, empty until the tracks load, so the map fits the world
          // from its first draw rather than the stations.
          satellites={{ tracks: live.tracks.value?.satellites ?? EMPTY_TRACKS, nowMs: now.getTime() }}
        />
      )}
      <div className="figures">
        <div className="figure">
          <h3>Stations</h3>
          {stations === null ? <p className="dim">Loading the station directory…</p> : <Fleet stations={stations} />}
        </div>
        <div className="figure">
          <h3>Orbits</h3>
          {live.tracks.value !== null && <Orbits tracks={live.tracks.value} />}
          {live.conditions.value !== null && <Kp conditions={live.conditions.value} />}
        </div>
      </div>
    </>
  );
}

function FiguresSection({ live }: { live: LiveReads }) {
  const aggregates = live.aggregates.value;
  if (aggregates === null) {
    return <p className="dim">Loading the network&apos;s figures…</p>;
  }
  return (
    <>
      <h3>Today</h3>
      <div className="figures">
        <TodayFigures population={aggregates.measured} marginS={aggregates.settleMarginS} />
        <TodayFigures population={aggregates.simulated} marginS={aggregates.settleMarginS} />
      </div>
      <h3>This week</h3>
      <div className="figures">
        <Week population={aggregates.measured} />
        <Week population={aggregates.simulated} />
      </div>
    </>
  );
}

export function Live({ go }: { go: Go }) {
  const live = useLive();
  const now = useNow(1000);
  return (
    <section aria-labelledby="live-heading" className={snapshotClass("view", live.snapshot)}>
      <h2 id="live-heading">Live</h2>
      <SnapshotNote snapshot={live.snapshot} what="This view shows" />
      {live.errors.map((one) => (
        <p role="alert" key={one}>
          {one}
        </p>
      ))}
      <NowSection live={live} now={now} go={go} />
      <FiguresSection live={live} />
      <h3>What just happened</h3>
      {live.events.value !== null && <Feed events={live.events.value} names={live.names} go={go} />}
      <h3>Receive-chain warnings</h3>
      {live.warnings.value !== null && <Warnings warnings={live.warnings.value} go={go} />}
      <p className="dim">
        Every settled pass, and the thirty days&apos; capture rate and loss budget, are on{" "}
        <a href="#/results">Results</a>.
      </p>
    </section>
  );
}

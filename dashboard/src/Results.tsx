import { useState } from "react";

import { formatDay, formatWindow } from "./format";
import { causeWords } from "./losses";
import { LossesSection } from "./Losses";
import {
  describeBudget,
  describeCapture,
  fetchNetworkReliability,
  type PopulationSummary,
} from "./reliability";
import {
  classWords,
  effectWords,
  fetchResults,
  mergePages,
  type PassResult,
  type ResultPage,
} from "./results";
import type { Route } from "./route";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
import { usePolled } from "./usePolled";

// Every settled pass across the network and what became of it, the thirty
// days' capture rate and loss budget, and every diagnosed loss (D-365). This is
// the network-wide results view Stage 24 owed. Measured and simulated stations
// are stated apart (rule 5); every absence is a sentence (D-086).

type Go = (route: Route) => void;

export function Population({ population }: { population: PopulationSummary }) {
  const name = population.simulated ? "Simulated stations" : "Measured stations";
  return (
    <div className="figure">
      <h4>
        {name} {population.simulated && <SimBadge />}
      </h4>
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

function ThirtyDays() {
  const reliability = usePolled("reliability", fetchNetworkReliability);
  const value = reliability.value;
  if (value === null) {
    return reliability.error === null ? <p className="dim">Loading the reliability figures…</p> : <p role="alert">{reliability.error}</p>;
  }
  return (
    <div className="figures">
      <Population population={value.measured} />
      <Population population={value.simulated} />
      <p className="dim">
        Over {formatDay(value.windowStart)} to {formatDay(value.windowEnd)}. A pass counts once its settle margin has
        passed, so the newest passes are not in it yet.
      </p>
    </div>
  );
}

export function ResultRow({ result, go }: { result: PassResult; go: Go }) {
  return (
    <tr>
      <td>{formatWindow(result.windowStart, result.windowEnd)}</td>
      <td>
        <button
          type="button"
          className="link mono"
          onClick={() => {
            go({ view: "stations", stationId: result.stationId });
          }}
        >
          {result.stationId}
        </button>{" "}
        {result.simulated && <SimBadge />}
      </td>
      <td className="mono">{result.satelliteId}</td>
      <td>
        <strong>{classWords(result.classification)}</strong>
        <span className="dim">, {effectWords(result.effect)}</span>
        {result.cause !== null && (
          <p className="dim">Diagnosed: {result.cause === "undetermined" ? "undetermined" : causeWords(result.cause)}</p>
        )}
      </td>
      <td>{result.listeningConfirmed ? "Yes" : "No"}</td>
    </tr>
  );
}

/** Pages older than the newest, loaded on request and never polled. */
function useOlder(nextCursor: string | null) {
  const [older, setOlder] = useState<ResultPage>({ results: [], nextCursor: null });
  const [error, setError] = useState<string | null>(null);
  const cursor = older.results.length > 0 ? older.nextCursor : nextCursor;
  const load = () => {
    if (cursor === null) return;
    // Not through a snapshot watch: an older page is not warmed through the front
    // (D-283), so in an outage it fails and says so, rather than being a copy.
    fetchResults((url, init) => fetch(url, init), cursor, new AbortController().signal)
      .then((page) => {
        setOlder((held) => ({ results: [...held.results, ...page.results], nextCursor: page.nextCursor }));
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(String(reason));
      });
  };
  return { older: older.results, more: cursor !== null, load, error };
}

function ResultTable({ go }: { go: Go }) {
  const newest = usePolled("results", (fetcher, signal) => fetchResults(fetcher, null, signal));
  const { older, more, load, error } = useOlder(newest.value?.nextCursor ?? null);
  if (newest.value === null) {
    return newest.error === null ? <p className="dim">Loading the results…</p> : <p role="alert">{newest.error}</p>;
  }
  const results = mergePages(newest.value.results, older);
  if (results.length === 0) {
    return <p className="dim">No pass has settled yet. A pass settles once its settle margin has passed.</p>;
  }
  return (
    <>
      <SnapshotNote snapshot={newest.snapshot} what="This list shows" />
      <div className={snapshotClass("table-scroll", newest.snapshot)}>
        <table>
          <caption>The most recently settled first.</caption>
          <thead>
            <tr>
              <th scope="col">Window (UTC)</th>
              <th scope="col">Station</th>
              <th scope="col">Satellite</th>
              <th scope="col">Result</th>
              <th scope="col">Listening confirmed</th>
            </tr>
          </thead>
          <tbody>
            {results.map((one) => (
              <ResultRow key={one.classificationId} result={one} go={go} />
            ))}
          </tbody>
        </table>
      </div>
      {error !== null && <p role="alert">{error}</p>}
      {more && (
        <button type="button" onClick={load}>
          Show older passes
        </button>
      )}
    </>
  );
}

export function ResultsView({ go }: { go: Go }) {
  return (
    <section aria-labelledby="results-heading" className="view">
      <h2 id="results-heading">Results</h2>
      <h3>The last thirty days</h3>
      <ThirtyDays />
      <h3>Every settled pass</h3>
      <ResultTable go={go} />
      <LossesSection go={go} />
    </section>
  );
}

import { AreaMap } from "./AreaMap";
import {
  describeInterval,
  fetchAreas,
  formatValue,
  quantityWords,
  seriesByQuantity,
  verdictWords,
  type Area,
  type AreaChange,
  type AreasRead,
  type AreasSummary,
  type SeriesPoint,
} from "./areas";
import { formatDay } from "./format";
import type { Route } from "./route";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
import { Sparkline } from "./Sparkline";
import { usePolled, type Polled } from "./usePolled";

// The published areas of interest (D-326, accepted D-366): each one's public
// products over time, its changes with their intervals, and every source with
// its terms (D-134). Nothing here is a station's or simulated, and nothing is
// computed on the page: every figure is the sealed report's (rule 8). A
// value a product did not publish is a gap with its reason, never a zero.

type Go = (route: Route) => void;

/** Areas are refreshed rarely: the summary changes when a person publishes one. */
const AREAS_REFRESH_MS = 300_000;

export function ChangeLine({ change, label }: { change: AreaChange; label: string | null }) {
  return (
    <li>
      <strong>
        {label !== null && `${label}: `}
        {quantityWords(change.quantity)}
      </strong>{" "}
      {verdictWords(change.verdict)}. Mean {change.baselineMean === null ? "none" : String(change.baselineMean)} from{" "}
      {formatDay(change.baseline.from)} to {formatDay(change.baseline.until)}, then{" "}
      {change.currentMean === null ? "none" : String(change.currentMean)} to {formatDay(change.current.until)}: change{" "}
      {describeInterval(change)}. <span className="dim">{change.reason}.</span>
    </li>
  );
}

function SeriesTable({ points }: { points: SeriesPoint[] }) {
  return (
    <details>
      <summary>Every period</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">Value</th>
            <th scope="col">Values behind it</th>
            <th scope="col">Sources</th>
          </tr>
        </thead>
        <tbody>
          {points.map((one) => (
            <tr key={one.periodFrom}>
              <th scope="row">{formatDay(one.periodFrom)}</th>
              <td>{one.value === null ? `none: ${one.missingReason ?? "no reason given"}` : formatValue(one.value, one.unit)}</td>
              <td>{one.count}</td>
              <td className="mono">{one.sources.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function Quantity({ quantity, points }: { quantity: string; points: SeriesPoint[] }) {
  const values = points.map((one) => one.value);
  const unit = points[0]?.unit ?? "";
  return (
    <div className="figure">
      {values.every((one) => one === null) ? (
        <p className="dim">{quantityWords(quantity)}: no period had a value.</p>
      ) : (
        <Sparkline
          title={quantityWords(quantity)}
          values={values}
          settled={points.map(() => true)}
          labels={points.map((one) => formatDay(one.periodFrom))}
          min={Math.min(0, ...values.map((one) => one ?? 0))}
          max={Math.max(...values.map((one) => one ?? 0), Number.EPSILON)}
          format={(value) => (value === null ? "none" : formatValue(value, unit))}
          latest="latest"
        />
      )}
      <SeriesTable points={points} />
    </div>
  );
}

function AreaDetail({ area, summary, go }: { area: Area; summary: AreasSummary; go: Go }) {
  const series = seriesByQuantity(summary.series, area.areaId);
  const changes = summary.changes.filter((one) => one.areaId === area.areaId);
  const [west, south, east, north] = area.bbox;
  return (
    <>
      <h3>{area.label}</h3>
      <p>
        {area.areaKm2.toFixed(0)} km², within {south}° to {north}° latitude and {west}° to {east}° longitude.{" "}
        <button
          type="button"
          className="link"
          onClick={() => {
            go({ view: "areas", stationId: null });
          }}
        >
          Every area
        </button>
      </p>
      <h4>Changes</h4>
      {changes.length === 0 ? (
        <p className="dim">No quantity of this area has a change rule.</p>
      ) : (
        <ul>
          {changes.map((one) => (
            <ChangeLine key={one.quantity} change={one} label={null} />
          ))}
        </ul>
      )}
      <div className="figures">
        {[...series.entries()].map(([quantity, points]) => (
          <Quantity key={quantity} quantity={quantity} points={points} />
        ))}
      </div>
      {series.size === 0 && <p className="dim">No public product has a value for this area yet.</p>}
    </>
  );
}

export function Sources({ summary }: { summary: AreasSummary }) {
  return (
    <>
      <h3>Sources and their terms</h3>
      <ul>
        {summary.sources.map((one) => (
          <li key={one.sourceId}>
            <span className="mono">{one.sourceId}</span>, {one.licence}: <a href={one.termsUrl}>terms</a>
          </li>
        ))}
      </ul>
      <p className="dim">
        From the regional report <span className="mono">{summary.reportSha256.slice(0, 12)}</span>, covering{" "}
        {formatDay(summary.since)} to {formatDay(summary.asOf)}. Every value is a public product&apos;s, none is a
        station&apos;s.
      </p>
    </>
  );
}

function Published({ summary, areaId, go }: { summary: AreasSummary; areaId: number | null; go: Go }) {
  const area = summary.areas.find((one) => one.areaId === areaId) ?? null;
  return (
    <>
      {summary.areas.length === 0 && <p className="dim">The published summary names no area.</p>}
      <AreaMap
        areas={summary.areas}
        selected={areaId}
        onSelect={(id) => {
          go({ view: "areas", stationId: null, areaId: id });
        }}
      />
      {area === null ? (
        <ul>
          {summary.areas.map((one) => (
            <li key={one.areaId}>
              <a href={`#/areas/${String(one.areaId)}`}>{one.label}</a>, {one.areaKm2.toFixed(0)} km²
            </li>
          ))}
        </ul>
      ) : (
        <AreaDetail area={area} summary={summary} go={go} />
      )}
      <Sources summary={summary} />
    </>
  );
}

function AreasBody({ read, areaId, go }: { read: Polled<AreasRead>; areaId: number | null; go: Go }) {
  if (read.value === null) {
    return read.error === null ? <p className="dim">Loading the areas of interest…</p> : null;
  }
  if ("notYetComputed" in read.value) {
    return <p className="dim">{read.value.notYetComputed}</p>;
  }
  return <Published summary={read.value.summary} areaId={areaId} go={go} />;
}

export function AreasView({ areaId, go }: { areaId: number | null; go: Go }) {
  const read = usePolled("areas", fetchAreas, AREAS_REFRESH_MS);
  return (
    <section aria-labelledby="areas-heading" className={snapshotClass("view", read.snapshot)}>
      <h2 id="areas-heading">Areas</h2>
      <SnapshotNote snapshot={read.snapshot} what="This view shows" />
      {read.error !== null && <p role="alert">{read.error}</p>}
      <AreasBody read={read} areaId={areaId} go={go} />
    </section>
  );
}

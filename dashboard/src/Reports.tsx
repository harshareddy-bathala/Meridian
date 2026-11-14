import { useState } from "react";

import { ChangeLine } from "./Areas";
import {
  type AreasRead,
  type AreasSummary,
  fetchAreas,
  formatValue,
  quantityWords,
  seriesByQuantity,
} from "./areas";
import { CriteriaPanel } from "./Criteria";
import { fetchCriteria } from "./criteria";
import { formatDay } from "./format";
import { fetchInsights } from "./insights";
import { ModelPanel } from "./ModelSummary";
import { blocksOf, fetchWeekOf, fetchWeekly, type NetworkWeekly } from "./reports";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass } from "./snapshot";
import { usePolled, type Polled } from "./usePolled";

// The reports, for a reader who wants it said in words: the network's latest
// settled week (D-367), the published area reports (D-366), and the sealed
// evaluation: its success criteria (D-368) and its model summary (D-332). Every text and figure is the platform's or a
// sealed report's; the page writes no sentence of its own about the data, and a
// simulated section is badged as well as said (rule 5).

/** A settled week changes only with a late diagnosis; areas and the model with a
 * publication. */
const REPORTS_REFRESH_MS = 300_000;

export function WeekText({ week }: { week: NetworkWeekly }) {
  return (
    <div className="report-text">
      <h4>{week.subject}</h4>
      {blocksOf(week.body).map((block) => (
        <div key={block.lines[0]} className="report-block">
          {block.lines.map((line, index) => (
            <p key={line} className={index === 0 && block.headed ? "report-heading" : undefined}>
              {line} {index === 0 && block.simulated && <SimBadge title="This section counts simulated stations" />}
            </p>
          ))}
        </div>
      ))}
      <p className="dim">
        Facts <span className="mono">{week.inputsSha256.slice(0, 12)}</span>, text{" "}
        <span className="mono">{week.contentSha256.slice(0, 12)}</span>, read {formatDay(week.asOf)}. Two reads that
        found the same records give the same hashes.
      </p>
    </div>
  );
}

/** The latest settled week, or an earlier one by its Monday: one read per week,
 * so the latest is never shown under an earlier week's name. */
function useWeek(monday: string | null): Polled<NetworkWeekly> {
  return usePolled(
    `weekly-${monday ?? "latest"}`,
    (fetcher, signal) => (monday === null ? fetchWeekly(fetcher, signal) : fetchWeekOf(fetcher, monday, signal)),
    REPORTS_REFRESH_MS,
  );
}

interface WeekProps {
  read: Polled<NetworkWeekly>;
  monday: string | null;
  choose: (monday: string | null) => void;
}

function WeekButtons({ week, monday, choose }: { week: NetworkWeekly } & Omit<WeekProps, "read">) {
  return (
    <p>
      {week.previousWeek !== null && (
        <button
          type="button"
          onClick={() => {
            choose(week.previousWeek);
          }}
        >
          The week before
        </button>
      )}{" "}
      {monday !== null && (
        <button
          type="button"
          onClick={() => {
            choose(null);
          }}
        >
          The latest week
        </button>
      )}
    </p>
  );
}

function NetworkWeek({ read, monday, choose }: WeekProps) {
  const week = read.value;
  if (week === null) {
    return read.error === null ? <p className="dim">Loading the weekly report…</p> : null;
  }
  return (
    <>
      <WeekText week={week} />
      <WeekButtons week={week} monday={monday} choose={choose} />
    </>
  );
}

function Latest({ summary, areaId }: { summary: AreasSummary; areaId: number }) {
  const quantities = [...seriesByQuantity(summary.series, areaId).entries()];
  return (
    <ul>
      {quantities.map(([quantity, points]) => {
        const valued = points.filter((one) => one.value !== null);
        const last = valued[valued.length - 1];
        return (
          <li key={quantity}>
            {quantityWords(quantity)}:{" "}
            {last?.value === undefined || last.value === null
              ? "no value published"
              : `${formatValue(last.value, last.unit)} for ${formatDay(last.periodFrom)}`}
            , {points.length} periods
          </li>
        );
      })}
    </ul>
  );
}

export function AreaReports({ read }: { read: AreasRead }) {
  if ("notYetComputed" in read) {
    return <p className="dim">{read.notYetComputed}</p>;
  }
  const { summary } = read;
  return (
    <>
      <p>
        The regional report <span className="mono">{summary.reportSha256.slice(0, 12)}</span>, covering{" "}
        {formatDay(summary.since)} to {formatDay(summary.asOf)}. Every value is a public product&apos;s, none is a
        station&apos;s.
      </p>
      {summary.areas.length === 0 && <p className="dim">The published report names no area.</p>}
      {summary.areas.map((area) => (
        <div key={area.areaId} className="figure">
          <h4>
            <a href={`#/areas/${String(area.areaId)}`}>{area.label}</a>, {area.areaKm2.toFixed(0)} km²
          </h4>
          <Latest summary={summary} areaId={area.areaId} />
          <ul>
            {summary.changes
              .filter((one) => one.areaId === area.areaId)
              .map((one) => (
                <ChangeLine key={one.quantity} change={one} label={null} />
              ))}
          </ul>
        </div>
      ))}
    </>
  );
}

export function ReportsView() {
  const [monday, setMonday] = useState<string | null>(null);
  const week = useWeek(monday);
  const areas = usePolled("areas", fetchAreas, REPORTS_REFRESH_MS);
  const criteria = usePolled("criteria", fetchCriteria, REPORTS_REFRESH_MS);
  const model = usePolled("insights", fetchInsights, REPORTS_REFRESH_MS);
  const snapshot = [areas, criteria, model].reduce((held, one) => oldest(held, one.snapshot), week.snapshot);
  return (
    <section aria-labelledby="reports-heading" className={snapshotClass("view", snapshot)}>
      <h2 id="reports-heading">Reports</h2>
      <SnapshotNote snapshot={snapshot} what="This view shows" />
      {[week.error, areas.error, criteria.error, model.error]
        .filter((one) => one !== null)
        .map((one) => (
          <p role="alert" key={one}>
            {one}
          </p>
        ))}
      <h3>The network&apos;s week</h3>
      <NetworkWeek read={week} monday={monday} choose={setMonday} />
      <h3>Area reports</h3>
      {areas.value !== null && <AreaReports read={areas.value} />}
      <h3>The evaluation</h3>
      {criteria.value !== null && <CriteriaPanel read={criteria.value} />}
      <div className="figures">{model.value !== null && <ModelPanel read={model.value} />}</div>
    </section>
  );
}

// Today's passes and the week by day, for measured and simulated stations apart
// (D-362). Every figure drawn from simulated rows carries the badge (rule 5),
// and every absence is a sentence (D-086).

import {
  describeMargin,
  todaysOutcomes,
  type DayFigures,
  type PopulationAggregates,
} from "./aggregates";
import { formatDay } from "./format";
import { SimBadge } from "./SimBadge";
import { Sparkline } from "./Sparkline";

function populationName(population: PopulationAggregates) {
  return (
    <h4>
      {population.simulated ? "Simulated stations" : "Measured stations"} {population.simulated && <SimBadge />}
    </h4>
  );
}

export function TodayFigures({ population, marginS }: { population: PopulationAggregates; marginS: number }) {
  const { today } = population;
  if (today.scheduled === 0) {
    return (
      <div className="figure">
        {populationName(population)}
        <p className="dim">No pass is scheduled to close today.</p>
      </div>
    );
  }
  const outcomes = todaysOutcomes(today);
  return (
    <div className="figure">
      {populationName(population)}
      <dl>
        <dt>Scheduled to close today</dt>
        <dd>
          {today.scheduled}, {today.ended} closed so far
        </dd>
        <dt>Decoded</dt>
        <dd>{outcomes.decoded}</dd>
        <dt>Lost</dt>
        <dd>{outcomes.lost}</dd>
        <dt>The satellite&apos;s own</dt>
        <dd>{outcomes.satellite}</dd>
        <dt>A station confirmed listening</dt>
        <dd>
          {today.listeningConfirmed} of {today.classified} classified
        </dd>
        <dt>Waiting to be classified</dt>
        <dd>{today.awaiting}</dd>
      </dl>
      {today.awaiting > 0 && (
        <p className="dim">
          A pass is classified once {describeMargin(marginS)} has passed since it closed, so a report still on its
          way is not read as absence.
        </p>
      )}
    </div>
  );
}

function budgetUsed(day: DayFigures): number | null {
  return day.budget.allowed > 0 ? day.budget.spent / day.budget.allowed : null;
}

function percent(value: number | null): string {
  return value === null ? "nothing to count" : `${String(Math.round(value * 100))} %`;
}

function DayTable({ days }: { days: DayFigures[] }) {
  return (
    <details>
      <summary>Figures by day</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Capture rate</th>
            <th scope="col">Budget used</th>
            <th scope="col">Settled</th>
          </tr>
        </thead>
        <tbody>
          {days.map((day) => (
            <tr key={day.day}>
              <th scope="row">{formatDay(day.day)}</th>
              <td>
                {percent(day.capture.estimate)}
                {day.capture.denominator > 0 && ` (${String(day.capture.numerator)} of ${String(day.capture.denominator)})`}
              </td>
              <td>
                {day.budget.eligible > 0 ? `${String(day.budget.spent)} of ${day.budget.allowed.toFixed(1)}` : "—"}
              </td>
              <td>{day.settled ? "yes" : "not yet"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

export function Week({ population }: { population: PopulationAggregates }) {
  const { days } = population;
  const labels = days.map((day) => formatDay(day.day));
  const settled = days.map((day) => day.settled);
  if (days.every((day) => day.passes === 0)) {
    return (
      <div className="figure">
        {populationName(population)}
        <p className="dim">No pass was classified this week.</p>
      </div>
    );
  }
  const used = days.map(budgetUsed);
  return (
    <div className="figure">
      {populationName(population)}
      <Sparkline
        title="Capture rate"
        values={days.map((day) => day.capture.estimate)}
        settled={settled}
        labels={labels}
        max={1}
        format={percent}
      />
      <Sparkline
        title="Loss budget used"
        values={used}
        settled={settled}
        labels={labels}
        max={Math.max(1, ...used.map((one) => one ?? 0))}
        reference={1}
        format={percent}
      />
      <DayTable days={days} />
    </div>
  );
}

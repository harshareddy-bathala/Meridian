import { describeSample, fetchConditions, type Conditions, type Sample } from "./conditions";
import { formatDay } from "./format";
import { type CalibrationBin, type Configuration, fetchInsights, type InsightsRead } from "./insights";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass } from "./snapshot";
import { usePolled } from "./usePolled";

// The world outside the stations, and how well the yield model knows it.
//
// Conditions are published by others and credited to them (D-134); none is
// Meridian's own measurement. The model's figures come from a sealed run, with
// the label that says who measured the receptions behind them (D-332), and a
// configuration the run could not support is shown as its reason, never zero.

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

function ConditionsPanel({ conditions }: { conditions: Conditions }) {
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

const SIDE = 200;

function Diagram({ bins }: { bins: CalibrationBin[] }) {
  const filled = bins.filter((one) => one.meanPredicted !== null && one.observed !== null);
  return (
    <svg className="calibration" viewBox={`0 0 ${String(SIDE)} ${String(SIDE)}`} role="img"
      aria-label="Reliability diagram: observed against predicted, one point per bin">
      <line className="calibration-ideal" x1={0} y1={SIDE} x2={SIDE} y2={0} />
      {filled.map((one) => (
        <circle key={one.low} cx={(one.meanPredicted ?? 0) * SIDE} cy={SIDE - (one.observed ?? 0) * SIDE} r={3 + Math.min(6, one.n / 20)} />
      ))}
    </svg>
  );
}

function ConfigurationFigure({ configuration }: { configuration: Configuration }) {
  if (!configuration.computable) {
    return (
      <li>
        Configuration {configuration.name}: not computable. {configuration.reason}
      </li>
    );
  }
  return (
    <li>
      Configuration {configuration.name}: Brier {configuration.brier.toFixed(3)} against the base rate's{" "}
      {configuration.baseBrier.toFixed(3)}
      {configuration.skill === null ? ", skill not computed" : `, skill ${configuration.skill.toFixed(2)}`}, on{" "}
      {configuration.nTest} held-out passes.
      <Diagram bins={configuration.bins} />
    </li>
  );
}

function ModelPanel({ read }: { read: InsightsRead }) {
  if ("notYetComputed" in read) {
    return (
      <div className="figure">
        <h3>The yield model</h3>
        <p className="dim">Not yet computed. {read.notYetComputed}</p>
      </div>
    );
  }
  const run = read.computed;
  return (
    <div className="figure">
      <h3>
        The yield model {run.simulated && <SimBadge />}
      </h3>
      <p className="label-note">{run.label}</p>
      <p className="dim">
        Completeness ratio {run.completenessRatio === null ? "not computed" : run.completenessRatio.toFixed(2)}. Run{" "}
        <span className="mono">{run.runSha256.slice(0, 12)}</span>, {run.method}, receptions from {run.sourceName}.
      </p>
      <ul>
        {run.configurations.map((one) => (
          <ConfigurationFigure key={one.name} configuration={one} />
        ))}
      </ul>
    </div>
  );
}

export function InsightsView() {
  const conditions = usePolled("conditions", fetchConditions);
  const insights = usePolled("insights", fetchInsights);
  const snapshot = oldest(conditions.snapshot, insights.snapshot);
  return (
    <section aria-labelledby="insights-heading" className={snapshotClass("view", snapshot)}>
      <h2 id="insights-heading">Insights</h2>
      <SnapshotNote snapshot={snapshot} what="This view shows" />
      {[conditions.error, insights.error].filter((one) => one !== null).map((one) => (
        <p role="alert" key={one}>
          {one}
        </p>
      ))}
      <div className="figures">
        {conditions.value !== null && <ConditionsPanel conditions={conditions.value} />}
        {insights.value !== null && <ModelPanel read={insights.value} />}
      </div>
    </section>
  );
}

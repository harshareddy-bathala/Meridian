import { type CalibrationBin, type Configuration, type InsightsRead } from "./insights";
import { SimBadge } from "./SimBadge";

// How well the yield model knows the passes: a sealed run's summary (D-332).
//
// The figures come from a sealed run, with the label that says who measured the
// receptions behind them, and a configuration the run could not support is
// shown as its reason, never zero (D-086).

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

export function ModelPanel({ read }: { read: InsightsRead }) {
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

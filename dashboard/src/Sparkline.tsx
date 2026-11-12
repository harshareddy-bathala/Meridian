// One series over a few days, small enough to sit beside its number.
//
// Ink on the page's ground, as every figure here is: the one series needs no
// colour of its own, and its title names it. A day still inside its settle
// margin is drawn hollow, and the table beside the chart says so in words, so
// the difference never rests on the mark alone. A day with nothing to count
// is a gap, never a point at zero (D-086). Each point names its day and value
// on hover, and the hit area is larger than the mark.

const WIDTH = 168;
const HEIGHT = 44;
const PAD = 6;

interface SparklineProps {
  title: string;
  values: (number | null)[];
  settled: boolean[];
  labels: string[];
  max: number;
  /** The value at the bottom of the chart; 0 unless a series can go below it. */
  min?: number;
  /** A level worth seeing, such as a budget fully spent; drawn as a dashed rule. */
  reference?: number;
  format: (value: number | null) => string;
  /** What the last point is called in the caption: "today", "latest". */
  latest?: string;
}

interface Scale {
  min: number;
  max: number;
}

function point(index: number, value: number, count: number, scale: Scale): [number, number] {
  const x = count === 1 ? WIDTH / 2 : PAD + (index * (WIDTH - 2 * PAD)) / (count - 1);
  const span = scale.max - scale.min || 1;
  const share = (Math.min(Math.max(value, scale.min), scale.max) - scale.min) / span;
  return [x, HEIGHT - PAD - share * (HEIGHT - 2 * PAD)];
}

/** The line's runs, broken wherever a day has no value. */
function runs(values: (number | null)[], scale: Scale): string[] {
  const paths: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length > 1) paths.push(current.join(" "));
      current = [];
      return;
    }
    const [x, y] = point(index, value, values.length, scale);
    current.push(`${current.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length > 1) paths.push(current.join(" "));
  return paths;
}

export function Sparkline(props: SparklineProps) {
  const { title, values, settled, labels, max, min = 0, reference, format, latest = "today" } = props;
  const scale = { min, max };
  const last = values[values.length - 1] ?? null;
  const summary = `${title}, ${labels[0] ?? ""} to ${labels[labels.length - 1] ?? ""}; ${latest} ${format(last)}.`;
  return (
    <figure className="sparkline">
      <figcaption>
        {title}, {latest}: <strong>{format(last)}</strong>
      </figcaption>
      <svg viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`} role="img" aria-label={summary}>
        {reference !== undefined && (
          <line
            className="sparkline-reference"
            x1={PAD}
            x2={WIDTH - PAD}
            y1={point(0, reference, 2, scale)[1]}
            y2={point(0, reference, 2, scale)[1]}
          />
        )}
        {runs(values, scale).map((d) => (
          <path key={d} className="sparkline-line" d={d} />
        ))}
        {values.map((value, index) => {
          if (value === null) return null;
          const [x, y] = point(index, value, values.length, scale);
          const label = labels[index] ?? "";
          return (
            <g key={label} className={settled[index] === true ? "sparkline-day" : "sparkline-day sparkline-unsettled"}>
              <title>{`${label}: ${format(value)}${settled[index] === true ? "" : ", not settled yet"}`}</title>
              <circle className="sparkline-hit" cx={x} cy={y} r={8} />
              <circle className="sparkline-mark" cx={x} cy={y} r={3.5} />
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

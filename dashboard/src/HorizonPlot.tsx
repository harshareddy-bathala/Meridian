import { horizonPath, skyPoint, type LearnedHorizon, type StationProfiles } from "./profiles";

const RADIUS = 90;
const LABELS: [string, number][] = [
  ["N", 0],
  ["E", 90],
  ["S", 180],
  ["W", 270],
];
const EDGE = RADIUS + 12;

function SkyPlot({ profiles }: { profiles: StationProfiles }) {
  const { declared, learned } = profiles;
  return (
    <svg
      viewBox={`${String(-EDGE)} ${String(-EDGE)} ${String(2 * EDGE)} ${String(2 * EDGE)}`}
      role="img"
      aria-label="Sky plot of the station's declared and learned horizon"
    >
      {[0, 30, 60].map((elevation) => (
        <circle key={elevation} className="horizon-ring" r={((90 - elevation) / 90) * RADIUS} />
      ))}
      {learned !== null && <path className="horizon-learned" d={horizonPath(learned.bins, RADIUS)} />}
      {declared.map((bins, index) => (
        <path key={index} className="horizon-declared" d={horizonPath(bins, RADIUS)} />
      ))}
      {LABELS.map(([label, azimuth]) => {
        const [x, y] = skyPoint(azimuth, -6, RADIUS + 8);
        return (
          <text key={label} x={x} y={y} className="horizon-label" textAnchor="middle" dominantBaseline="middle">
            {label}
          </text>
        );
      })}
    </svg>
  );
}

function LearnedCaption({ learned }: { learned: LearnedHorizon | null }) {
  if (learned === null) {
    return <>No learned horizon yet.</>;
  }
  const heard = learned.bins.reduce((total, bin) => total + (bin.sampleCount ?? 0), 0);
  return (
    <>
      Shaded: learned from {heard} detection{heard === 1 ? "" : "s"} up to {learned.trainedUntil.slice(0, 10)},
      which informs prediction only.
    </>
  );
}

/**
 * The declared horizon as a dashed line and the learned one as a shaded area,
 * on one sky plot. They are drawn apart because one is a claim and the other is
 * evidence (D-031), and only the claim stops a pass being scheduled (D-175).
 */
export function HorizonPlot({ profiles }: { profiles: StationProfiles }) {
  if (profiles.declared.length === 0 && profiles.learned === null) {
    return <p className="dim">No horizon yet: nothing declared, and no labelled dataset built into a profile.</p>;
  }
  return (
    <figure className="horizon">
      <SkyPlot profiles={profiles} />
      <figcaption>
        {profiles.simulated && <span className="badge badge-simulated">simulated</span>}{" "}
        {profiles.declared.length > 0 && <>Dashed: declared, which scheduling obeys. </>}
        <LearnedCaption learned={profiles.learned} />
      </figcaption>
    </figure>
  );
}

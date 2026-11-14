import { CLAIMS, type Criterion, type CriteriaRead, describeFigure, figureWords, verdictOf } from "./criteria";
import { formatDay } from "./format";
import { REPOSITORY } from "./releaseNotes";
import { SimBadge } from "./SimBadge";

// The success criteria a sealed evaluation run judged (D-368). Each says what it
// claims, whether this run measured it and on whose passes, its figures against
// their targets, and the run's own verdict; the page judges nothing. A target
// still to agree with the team is marked proposed, and one measured on
// simulated fault runs carries the badge (rule 5).

/** A section's reason, a lowercase fragment, as a sentence. */
function sentence(reason: string): string {
  return `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`;
}

function Measured({ criterion }: { criterion: Extract<Criterion, { status: "measured" }> }) {
  return (
    <>
      <ul>
        {criterion.figures.map((one) => (
          <li key={one.name}>
            {figureWords(one.name)}: {describeFigure(one)}
          </li>
        ))}
      </ul>
      {criterion.intervalClears === false && (
        <p className="dim">The interval reaches past the target, so the point alone meets it.</p>
      )}
      {criterion.caveat !== null && <p className="dim">Not claimed: {criterion.caveat}.</p>}
    </>
  );
}

export function CriterionItem({ criterion }: { criterion: Criterion }) {
  return (
    <li className="criterion">
      <strong>
        {criterion.id} {CLAIMS[criterion.id] ?? ""}
      </strong>
      {criterion.proposed && <span className="dim"> (target proposed)</span>}:{" "}
      <span className={criterion.status === "measured" && criterion.met ? "criterion-met" : undefined}>
        {verdictOf(criterion)}
      </span>
      {criterion.simulated && (
        <>
          {" "}
          <SimBadge title="Measured on simulated fault runs" />
        </>
      )}
      {criterion.status === "measured" ? (
        <Measured criterion={criterion} />
      ) : (
        <p className="dim">{sentence(criterion.reason)}</p>
      )}
    </li>
  );
}

export function CriteriaPanel({ read }: { read: CriteriaRead }) {
  if ("notYetComputed" in read) {
    return (
      <div className="figure">
        <h4>The success criteria</h4>
        <p className="dim">Not yet computed. {read.notYetComputed}</p>
      </div>
    );
  }
  const run = read.computed;
  return (
    <div className="figure">
      <h4>The success criteria</h4>
      <p className="dim">
        From the sealed run <span className="mono">{run.runSha256.slice(0, 12)}</span>, {run.method}, over{" "}
        {formatDay(run.since)} to {formatDay(run.asOf)}. Its verdicts are the run&apos;s own.
      </p>
      <ul className="criteria">
        {run.criteria.map((one) => (
          <CriterionItem key={one.id} criterion={one} />
        ))}
      </ul>
      <p className="dim">
        SC-6 and SC-10 are not judged by an evaluation run; their evidence is in{" "}
        <a href={`${REPOSITORY}docs/ACCEPTANCE.md`}>the acceptance record</a>.
      </p>
    </div>
  );
}

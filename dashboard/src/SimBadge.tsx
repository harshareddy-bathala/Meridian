/**
 * The one mark every figure drawn from simulated rows carries (CLAUDE.md rule
 * 5). Words, not a colour alone, so it survives a screenshot, a screen reader
 * and a greyscale print.
 */
interface SimBadgeProps {
  /** What is simulated, for the tooltip: "A virtual station run by the simulator". */
  title?: string;
}

export function SimBadge({ title = "From virtual stations run by the simulator" }: SimBadgeProps) {
  return (
    <span className="badge badge-simulated" title={title}>
      Simulated
    </span>
  );
}

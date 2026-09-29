"""Which decided passes a round decides again, and when that writes anything — D-171.

A round considers every pass in its horizon that is **open**:

* never decided under this configuration;
* skipped — a skip records why the pass lost, and a round has a reason to ask
  again when the pass it lost to has been declined, or its station has
  returned;
* revoked because its station was offline — the station is back, since only
  an available station's passes are decided, and did not name the assignment
  on its return, or it would have been reinstated.

Everything else is **closed**: work the station holds or has done, and an
assignment the station declined — offering a station the pass it just let go
would be asking it to decline again.

A decision about an open pass is written as a new revision only when it
changed: a skip that is skipped again while the assignment it named still
blocks it says nothing new, and a round writing it would fill the table with
copies every five minutes. "Still blocks it", not "is named again": a skip
names the best selection that overlapped it when it was made, and a later
round, where that selection is a commitment among others, may name another
one of them first. Anything else — a skip now taken, a skip whose blocker has
gone, a revoked pass decided at all — is a new row.

A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-003, D-165, D-171.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.scheduler import Commitment, ScheduleOutcome
from meridian.scheduler.constraints import overlaps
from meridian.store.schedule_reads import LatestDecision
from meridian.store.schedule_writes import NewAssignment

__all__ = ["blocking", "changed", "next_revision", "reopens", "unchanged"]


def reopens(latest: LatestDecision | None) -> bool:
    """Whether a pass with this latest decision is decided again."""
    if latest is None or latest.decision == "skipped":
        return True
    return latest.state == "revoked" and latest.revoked_reason == "offline"


def next_revision(latest: LatestDecision | None) -> int:
    """The revision a new decision about the pass is written as."""
    return 0 if latest is None else latest.revision + 1


def blocking(
    outcome: ScheduleOutcome,
    committed: Sequence[Commitment],
    selected_ids: Mapping[int, str],
    turnaround_s: float,
) -> dict[int, frozenset[str]]:
    """For each skip, every assignment overlapping it: commitments and selections.

    Args:
        outcome: The round's schedule.
        committed: The assignments earlier rounds made.
        selected_ids: Each selection's assignment id, by pass id.
        turnaround_s: The run's turnaround, which overlap is judged with.
    """
    found = {}
    for rejection in outcome.rejected:
        candidate = rejection.scored.candidate
        held = {
            one.assignment_id
            for one in committed
            if overlaps(one.candidate, candidate, turnaround_s)
        }
        held |= {
            selected_ids[one.candidate.pass_id]
            for one in outcome.selected
            if overlaps(one.candidate, candidate, turnaround_s)
        }
        found[candidate.pass_id] = frozenset(held)
    return found


def unchanged(
    latest: LatestDecision | None,
    row: NewAssignment,
    blockers: frozenset[str] = frozenset(),
) -> bool:
    """Whether a new decision says only what the latest one already did.

    Args:
        latest: The pass's latest stored decision, if any.
        row: The decision this round made.
        blockers: Every assignment that overlaps the pass this round, as
            :func:`blocking` finds them.
    """
    if latest is None or latest.decision != "skipped" or row.decision != "skipped":
        return False
    named = latest.conflicts_with_assignment_id
    return named == row.conflicts_with_assignment_id or named in blockers


def changed(
    decided: Sequence[NewAssignment],
    previous: Mapping[int, LatestDecision],
    outcome: ScheduleOutcome,
    committed: Sequence[Commitment],
    turnaround_s: float,
) -> list[NewAssignment]:
    """The decisions a round writes: every one but those :func:`unchanged` drops.

    Args:
        decided: Every decision of the round, as rows.
        previous: Each pass's latest stored decision.
        outcome: The round's schedule, which ``decided`` was made from.
        committed: The assignments earlier rounds made.
        turnaround_s: The run's turnaround.
    """
    ids = {one.pass_id: one.assignment_id for one in decided}
    blockers = blocking(outcome, committed, ids, turnaround_s)
    return [
        row
        for row in decided
        if not unchanged(
            previous.get(row.pass_id), row, blockers.get(row.pass_id, frozenset())
        )
    ]

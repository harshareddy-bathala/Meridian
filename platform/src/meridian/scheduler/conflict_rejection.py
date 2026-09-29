"""Taking ranked passes greedily, and recording what each rejection lost to.

A station has one antenna and one receiver, so two passes that overlap in time
cannot both be received. Given candidates already ordered best-first by a
ranking function, this walks them in order and takes each one that still fits,
rejecting the rest against whichever selection blocked them.

Greedy is the baseline's whole character. It is optimal only when the ranking
happens to agree with what a globally optimal allocation would choose, and it
does not in general — a single high pass can displace two decent ones worth more
together. That gap is what the Stage 18 optimiser exists to close, and it can
only be reported as a number because this simple rule is here to be measured
against.

The rules it keeps are those of :mod:`meridian.scheduler.constraints`, the ones
the optimiser keeps too: one antenna, turnaround included, and the delivery cap (D-166).

A leaf: no I/O, no clock, no database. It takes candidates and returns them, so
a schedule can be checked against overlapping windows written out by hand.

Reference: docs/ARCHITECTURE.md (non-overlap including slew and settling time);
docs/GLOSSARY.md on slew; docs/DECISIONS.md D-065, D-165, D-166.
"""

from __future__ import annotations

from collections.abc import Sequence

from meridian.scheduler import (
    Candidate,
    Commitment,
    Rejection,
    ScheduleOutcome,
    ScoredCandidate,
)
from meridian.scheduler.constraints import Rules, exceeds_cap, overlaps

__all__ = ["select_without_conflict"]


def _first_conflict(
    selected: Sequence[ScoredCandidate], candidate: Candidate, turnaround_s: float
) -> ScoredCandidate | None:
    """The best-ranked selection blocking ``candidate``, or None if none does.

    ``selected`` is in ranking order, so the first match is the highest-ranked
    one — which is the selection worth naming in the rejection, since it is the
    one an operator asking "why not this pass?" is owed.
    """
    for taken in selected:
        if overlaps(taken.candidate, candidate, turnaround_s):
            return taken
    return None


def _first_commitment(
    committed: Sequence[Commitment], candidate: Candidate, turnaround_s: float
) -> Commitment | None:
    """The earliest commitment blocking ``candidate``, or None if none does."""
    for commitment in committed:
        if overlaps(commitment.candidate, candidate, turnaround_s):
            return commitment
    return None


def _rejection(
    scored: ScoredCandidate,
    selected: Sequence[ScoredCandidate],
    committed: Sequence[Commitment],
    rules: Rules,
) -> Rejection | None:
    """Why ``scored`` cannot be taken now, or None if it can."""
    candidate = scored.candidate
    commitment = _first_commitment(committed, candidate, rules.turnaround_s)
    if commitment is not None:
        return Rejection(
            scored=scored,
            rule="overlap",
            conflicts_with_pass_id=commitment.candidate.pass_id,
            committed_assignment_id=commitment.assignment_id,
        )
    blocker = _first_conflict(selected, candidate, rules.turnaround_s)
    if blocker is not None:
        return Rejection(
            scored=scored,
            rule="overlap",
            conflicts_with_pass_id=blocker.candidate.pass_id,
        )
    held = [
        *(one.candidate for one in selected),
        *(one.candidate for one in committed),
    ]
    if exceeds_cap(candidate, held, rules):
        return Rejection(
            scored=scored, rule="eligible_cap", conflicts_with_pass_id=None
        )
    return None


def select_without_conflict(
    ranked: Sequence[ScoredCandidate],
    *,
    rules: Rules,
    committed: Sequence[Commitment] = (),
) -> ScheduleOutcome:
    """Take ranked candidates in order, skipping any that no longer fit.

    Args:
        ranked: Candidates already scored and ordered best-first — the output of
            a ranking function such as
            ``elevation_baseline.rank_by_elevation``. The order is obeyed, never
            re-derived: this function has no opinion about what is better, which
            is what lets one non-overlap rule serve every configuration in
            docs/EVALUATION.md §3.
        rules: The turnaround and the delivery cap (D-166). One turnaround for
            the whole run rather than one per station: ``stations`` has no
            column for it, and a fixed-antenna station's true value of zero is
            not something to guess at from ``station_capabilities.tracking``.
        committed: Assignments earlier runs already made. They are never
            displaced and never appear in the outcome; a candidate one of them
            blocks is rejected naming it, before any selection of this run is
            consulted (D-165).

    Returns:
        A :class:`~meridian.scheduler.ScheduleOutcome` holding the selections in
        the order they were taken and one :class:`~meridian.scheduler.Rejection`
        per displaced candidate, each naming the rule and, for an overlap, the
        assignment that displaced it.

    Note:
        **Every input appears in exactly one of the two output lists.** A
        candidate that were silently dropped would be a pass the platform
        considered and can no longer account for, and the dashboard screen that
        explains the schedule would have a hole in it with no way to detect one.

        Comparing each candidate against every selection so far is quadratic in
        the worst case. Over a day's horizon for one station that is a few
        hundred comparisons, and the alternative — an interval tree — buys
        nothing here except a structure a reader has to learn before they can
        check the rule.
    """
    selected: list[ScoredCandidate] = []
    rejected: list[Rejection] = []

    for scored in ranked:
        rejection = _rejection(scored, selected, committed, rules)
        if rejection is None:
            selected.append(scored)
        else:
            rejected.append(rejection)

    return ScheduleOutcome(selected=selected, rejected=rejected)

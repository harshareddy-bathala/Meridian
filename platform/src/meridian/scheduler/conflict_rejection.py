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

A leaf: no I/O, no clock, no database. It takes candidates and returns them, so
a schedule can be checked against overlapping windows written out by hand.

Reference: docs/ARCHITECTURE.md (non-overlap including slew and settling time);
docs/GLOSSARY.md on slew; docs/DECISIONS.md D-065.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from meridian.scheduler import (
    Candidate,
    Rejection,
    ScheduleOutcome,
    ScoredCandidate,
)

__all__ = ["conflicts_with", "select_without_conflict"]


def conflicts_with(one: Candidate, other: Candidate, turnaround_s: float) -> bool:
    """Whether one station could not physically receive both of these passes.

    Args:
        one: A candidate pass.
        other: The candidate to test it against.
        turnaround_s: Seconds the station needs between two receptions — slewing
            the antenna to the new bearing and letting it settle. Zero means
            passes may be taken back to back.

    Returns:
        True when the two windows, each opened out by ``turnaround_s``, overlap
        for a station that is the same station.

    Note:
        **Two passes for different stations never conflict.** The constraint is
        one antenna, not one network; the whole point of a network is that two
        stations take two passes at once.

        **Touching is not overlapping.** A pass ending at exactly the instant
        another begins conflicts only when ``turnaround_s`` is positive, which
        is the honest answer: with no slew to perform there is nothing in the
        way, and with slew to perform there is. Using ``<`` rather than ``<=``
        is what makes that boundary fall the right way, and it matters — passes
        of one satellite from one station recur on a near-fixed period, so
        near-abutting windows are common rather than exotic.
    """
    if one.station_id != other.station_id:
        return False

    room = timedelta(seconds=turnaround_s)
    return one.aos < other.los + room and other.aos < one.los + room


def _first_conflict(
    selected: Sequence[ScoredCandidate], candidate: Candidate, turnaround_s: float
) -> ScoredCandidate | None:
    """The best-ranked selection blocking ``candidate``, or None if none does.

    ``selected`` is in ranking order, so the first match is the highest-ranked
    one — which is the selection worth naming in the rejection, since it is the
    one an operator asking "why not this pass?" is owed.
    """
    for taken in selected:
        if conflicts_with(taken.candidate, candidate, turnaround_s):
            return taken
    return None


def select_without_conflict(
    ranked: Sequence[ScoredCandidate], *, turnaround_s: float
) -> ScheduleOutcome:
    """Take ranked candidates in order, skipping any that no longer fit.

    Args:
        ranked: Candidates already scored and ordered best-first — the output of
            a ranking function such as
            ``elevation_baseline.rank_by_elevation``. The order is obeyed, never
            re-derived: this function has no opinion about what is better, which
            is what lets one non-overlap rule serve every configuration in
            docs/EVALUATION.md §3.
        turnaround_s: Seconds a station needs between two receptions. One value
            for the whole run rather than one per station: turnaround is a
            property of the rotator and the antenna, ``stations`` has no column
            for it, and a fixed-antenna station's true value of zero is not
            something to guess at from ``station_capabilities.tracking``.

    Returns:
        A :class:`~meridian.scheduler.ScheduleOutcome` holding the selections in
        the order they were taken and one :class:`~meridian.scheduler.Rejection`
        per displaced candidate, each naming the selection that displaced it.

    Raises:
        ValueError: ``turnaround_s`` is negative, which would let two genuinely
            overlapping passes be scheduled together.

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
    if turnaround_s < 0:
        raise ValueError(f"turnaround_s is negative: {turnaround_s}")

    selected: list[ScoredCandidate] = []
    rejected: list[Rejection] = []

    for scored in ranked:
        blocker = _first_conflict(selected, scored.candidate, turnaround_s)
        if blocker is None:
            selected.append(scored)
            continue
        rejected.append(
            Rejection(
                scored=scored,
                conflicts_with_pass_id=blocker.candidate.pass_id,
            )
        )

    return ScheduleOutcome(selected=selected, rejected=rejected)

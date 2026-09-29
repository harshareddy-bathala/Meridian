"""The constraints every schedule obeys, and a check that one does.

Every scheduler — the Stage 7 baselines, the optimiser, the oracle — works to
the same rules, or D − B would measure two schedulers under different physics
and credit the difference to the model (D-065). So the rules live here, once:

* **one antenna** — a station's assignments never overlap. The window judged is
  the assignment's: the pass opened out by its timing uncertainty (D-021), plus
  the station's turnaround after it;
* **the delivery cap** — at no instant does a station hold more than
  :data:`MOST_ELIGIBLE` assignments that a heartbeat would deliver together,
  eligible meaning ``start ≤ t + DELIVERY_LEAD`` and ``end ≥ t`` (D-035);
* **availability** — a station that is ``offline`` is given nothing new;
* **commitments** — assignments earlier runs made are fixed (D-165).

The downlink rule — a live transmitter the station declared it can receive —
is applied when a pass becomes a candidate, so the candidate set is the set of
passes that satisfy it.

:func:`violations` checks an outcome against a :class:`Problem` without asking
how the outcome was found, and the scheduler run refuses to write one it
rejects. A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-021, D-035, D-065, D-165, D-166.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.scheduler import Candidate, Commitment, ScheduleOutcome

__all__ = [
    "DELIVERY_LEAD",
    "MOST_ELIGIBLE",
    "Problem",
    "Rules",
    "Violation",
    "exceeds_cap",
    "overlaps",
    "violations",
    "window",
]

MOST_ELIGIBLE = 8
"""How many assignments one station may hold eligible for delivery at once.

The number a heartbeat response carries (``MAX_ASSIGNMENTS_PER_RESPONSE``,
D-007), which a microcontroller station commits to at compile time. D-035 made
more than this an invariant broken rather than a queue to page through, and left
enforcing it to whoever creates assignments. A unit test holds the two equal.
"""

DELIVERY_LEAD = timedelta(hours=2)
"""How far ahead of its start an assignment is delivered (D-026, D-035)."""


@dataclass(frozen=True, slots=True)
class Rules:
    """The parameters of the constraints, fixed for a run."""

    turnaround_s: float
    """Seconds a station needs between two receptions (D-066)."""

    most_eligible: int = MOST_ELIGIBLE
    delivery_lead: timedelta = DELIVERY_LEAD

    def __post_init__(self) -> None:
        """Refuse parameters that would let a broken schedule through."""
        if self.turnaround_s < 0:
            raise ValueError(f"turnaround_s is negative: {self.turnaround_s}")
        if self.most_eligible < 1:
            raise ValueError(f"most_eligible is below 1: {self.most_eligible}")


@dataclass(frozen=True, slots=True)
class Problem:
    """Everything a schedule is checked against."""

    candidates: tuple[Candidate, ...]
    commitments: tuple[Commitment, ...]
    unavailable: frozenset[str]
    """Stations that may be given nothing new."""

    rules: Rules


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken rule, naming the passes that break it."""

    rule: str
    pass_ids: tuple[int, ...]
    detail: str


def window(candidate: Candidate) -> tuple[datetime, datetime]:
    """The assignment window: the pass opened out by its uncertainty (D-021)."""
    margin = timedelta(seconds=candidate.margin_s)
    return candidate.aos - margin, candidate.los + margin


def overlaps(one: Candidate, other: Candidate, turnaround_s: float) -> bool:
    """Whether one station could not receive both.

    Two windows conflict when either begins before the other has ended and the
    station has turned round. Touching is not overlapping, so with no
    turnaround two windows that abut exactly are compatible. Passes for two
    stations never conflict: the constraint is one antenna, not one network.
    """
    if one.station_id != other.station_id:
        return False
    room = timedelta(seconds=turnaround_s)
    one_start, one_end = window(one)
    other_start, other_end = window(other)
    return one_start < other_end + room and other_start < one_end + room


def _eligible(candidate: Candidate, lead: timedelta) -> tuple[datetime, datetime]:
    """The instants at which a heartbeat would deliver this assignment."""
    start, end = window(candidate)
    return start - lead, end


def _peak(intervals: Iterable[tuple[datetime, datetime]]) -> int:
    """The most closed intervals holding one instant in common."""
    # At one instant a start sorts before an end, so two intervals that only
    # touch are counted together: both are eligible at that instant.
    events = sorted(
        event for start, end in intervals for event in ((start, 0), (end, 1))
    )
    held = peak = 0
    for _, kind in events:
        if kind == 0:
            held += 1
            peak = max(peak, held)
        else:
            held -= 1
    return peak


def _peak_around(
    candidate: Candidate, others: Iterable[Candidate], rules: Rules
) -> int:
    """The most assignments eligible at once while ``candidate`` is, itself included."""
    low, high = _eligible(candidate, rules.delivery_lead)
    clipped = [(low, high)]
    for other in others:
        if other.station_id != candidate.station_id:
            continue
        start, end = _eligible(other, rules.delivery_lead)
        if start <= high and end >= low:
            clipped.append((max(start, low), min(end, high)))
    return _peak(clipped)


def exceeds_cap(candidate: Candidate, held: Sequence[Candidate], rules: Rules) -> bool:
    """Whether adding ``candidate`` to ``held`` breaks the delivery cap.

    ``held`` is what the station already has: this run's selections and the
    commitments. Only instants inside the candidate's own eligibility are
    counted, so assignments crowded together at some other time do not block
    it.
    """
    return _peak_around(candidate, held, rules) > rules.most_eligible


def violations(problem: Problem, outcome: ScheduleOutcome) -> tuple[Violation, ...]:
    """Every rule the outcome breaks, or nothing.

    Args:
        problem: The candidates, the commitments, the unavailable stations and
            the rules.
        outcome: A schedule for exactly those candidates.

    Returns:
        One violation per broken rule and set of passes, in a fixed order.
    """
    selected = [one.candidate for one in outcome.selected]
    found = [
        *_accounting(problem, outcome),
        *_unavailable(problem, selected),
        *_overlapping(problem, selected),
        *_over_cap(problem, selected),
    ]
    return tuple(found)


def _accounting(problem: Problem, outcome: ScheduleOutcome) -> list[Violation]:
    """Every candidate decided exactly once, and nothing else decided."""
    decided = Counter(
        [one.candidate.pass_id for one in outcome.selected]
        + [one.scored.candidate.pass_id for one in outcome.rejected]
    )
    posed = {one.pass_id for one in problem.candidates}
    committed = {one.candidate.pass_id for one in problem.commitments}
    found = []
    twice = sorted(pass_id for pass_id, count in decided.items() if count > 1)
    if twice:
        found.append(Violation("decided_twice", tuple(twice), "decided more than once"))
    missing = sorted(posed - decided.keys())
    if missing:
        found.append(Violation("undecided", tuple(missing), "a candidate left out"))
    stray = sorted(decided.keys() - posed)
    if stray:
        found.append(Violation("not_a_candidate", tuple(stray), "decided unasked"))
    retaken = sorted({one.candidate.pass_id for one in outcome.selected} & committed)
    if retaken:
        found.append(
            Violation("recommitted", tuple(retaken), "a commitment selected again")
        )
    return found


def _unavailable(problem: Problem, selected: Sequence[Candidate]) -> list[Violation]:
    taken = sorted(
        one.pass_id for one in selected if one.station_id in problem.unavailable
    )
    if not taken:
        return []
    return [Violation("unavailable", tuple(taken), "assigned to an offline station")]


def _overlapping(problem: Problem, selected: Sequence[Candidate]) -> list[Violation]:
    """Each pair of overlapping assignments with at least one of this run's."""
    fixed = [one.candidate for one in problem.commitments]
    turnaround_s = problem.rules.turnaround_s
    found = []
    for index, one in enumerate(selected):
        for other in [*selected[index + 1 :], *fixed]:
            if overlaps(one, other, turnaround_s):
                pair = tuple(sorted((one.pass_id, other.pass_id)))
                found.append(Violation("overlap", pair, "one antenna, two passes"))
    return found


def _over_cap(problem: Problem, selected: Sequence[Candidate]) -> list[Violation]:
    """Each selection during whose eligibility the station holds too many."""
    held = [*selected, *(one.candidate for one in problem.commitments)]
    found = []
    for one in selected:
        others = [other for other in held if other is not one]
        peak = _peak_around(one, others, problem.rules)
        if peak > problem.rules.most_eligible:
            found.append(
                Violation(
                    "eligible_cap",
                    (one.pass_id,),
                    f"{peak} eligible at once, above {problem.rules.most_eligible}",
                )
            )
    return found

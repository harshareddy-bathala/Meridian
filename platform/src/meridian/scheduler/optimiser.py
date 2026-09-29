"""The schedule that maximises total value, found by a solver and then checked.

Greedy takes the best pass first and lives with the consequences, so one high
pass can displace two that were worth more together. This finds the selection
whose summed score is largest under D-166's constraints, as a mixed-integer
programme solved by HiGHS (D-167):

* one binary variable per candidate, its score as its objective coefficient;
* for each station, one row per maximal set of mutually overlapping windows —
  the windows plus turnaround are intervals, so every overlap is covered by the
  sets holding some window's start — allowing at most one;
* for each station, one row per instant a window's eligibility begins, capping
  how many are eligible then at what the commitments leave of D-035's eight.

Candidates a commitment already blocks are rejected before the solver sees
them, naming it, as greedy does.

**A solver's answer is a claim.** The selection is checked by
:func:`~meridian.scheduler.constraints.violations`; a selection it rejects, or
no selection at all within the time limit, falls back to greedy under the same
constraints, and the run says so. Either way a valid schedule comes back.

**Deterministic for identical input.** Candidates are put in one canonical
order before the model is built, HiGHS runs on one thread with a fixed seed and
a zero relative gap, so the same candidates give the same model and the same
answer however they arrived. A time limit reached is the exception, and the
status says when it was.

Reference: docs/DECISIONS.md D-065, D-165, D-166, D-167.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from meridian.scheduler import (
    Candidate,
    Commitment,
    Rejection,
    ScheduleOutcome,
    ScoredCandidate,
)
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import (
    Problem,
    Rules,
    exceeds_cap,
    overlaps,
    violations,
)
from meridian.scheduler.programme import (
    SOLVER,
    Answer,
    SolverSettings,
    SolverStatus,
    solve,
    solver_version,
)

__all__ = ["Optimised", "SolverRun", "SolverSettings", "SolverStatus", "optimise"]


@dataclass(frozen=True, slots=True)
class SolverRun:
    """What the solver did, recorded with the schedule it produced."""

    status: SolverStatus
    solver: str
    version: str
    objective: float
    """The summed score of the schedule returned."""

    bound: float | None
    """The solver's proven upper bound; ``None`` on a fallback."""

    runtime_s: float
    time_limit_s: float
    detail: str | None
    """Why a fallback happened; ``None`` otherwise."""


@dataclass(frozen=True, slots=True)
class Optimised:
    """A valid schedule, and how it was found."""

    outcome: ScheduleOutcome
    run: SolverRun


def _ranking_key(scored: ScoredCandidate) -> tuple[float, object, int]:
    """Best first, then earlier, then by id: D-065's total order."""
    return (-scored.score, scored.candidate.aos, scored.candidate.pass_id)


def _canonical(scored: Sequence[ScoredCandidate]) -> list[ScoredCandidate]:
    """One order for the model, whatever order the candidates arrived in."""
    return sorted(
        scored,
        key=lambda one: (
            one.candidate.station_id,
            one.candidate.aos,
            one.candidate.pass_id,
        ),
    )


def _fits(candidate: Candidate, taken: Sequence[Candidate], rules: Rules) -> bool:
    """Whether ``candidate`` can join ``taken`` under every rule."""
    if any(overlaps(candidate, other, rules.turnaround_s) for other in taken):
        return False
    return not exceeds_cap(candidate, taken, rules)


def _outcome(
    free: Sequence[ScoredCandidate],
    chosen: frozenset[int],
    blocked: Sequence[Rejection],
    committed: Sequence[Commitment],
    rules: Rules,
) -> ScheduleOutcome:
    """The solver's choice as selections and reasoned rejections.

    A candidate left out that still fits is taken: at the optimum none does
    while scores are positive, and under a time limit this only improves the
    answer. Every rejection then has a reason — the best selection it overlaps,
    or the delivery cap.
    """
    selected = sorted((free[index] for index in chosen), key=_ranking_key)
    fixed = [one.candidate for one in committed]
    rest = sorted(
        (one for index, one in enumerate(free) if index not in chosen),
        key=_ranking_key,
    )
    for one in rest:
        taken = [*(chosen_one.candidate for chosen_one in selected), *fixed]
        if _fits(one.candidate, taken, rules):
            selected.append(one)
    # Ranked again, so the blocker named below is the best selection overlapping.
    selected.sort(key=_ranking_key)
    kept = {id(one) for one in selected}
    rejected = list(blocked)
    for one in rest:
        if id(one) in kept:
            continue
        blocker = next(
            (
                taken
                for taken in selected
                if overlaps(taken.candidate, one.candidate, rules.turnaround_s)
            ),
            None,
        )
        if blocker is None:
            rejected.append(Rejection(one, "eligible_cap", None))
        else:
            rejected.append(Rejection(one, "overlap", blocker.candidate.pass_id))
    return ScheduleOutcome(selected=selected, rejected=rejected)


def _blocked_by_commitments(
    scored: Sequence[ScoredCandidate], committed: Sequence[Commitment], rules: Rules
) -> tuple[list[ScoredCandidate], list[Rejection]]:
    """Split off the candidates an assignment already made rules out."""
    free, blocked = [], []
    for one in scored:
        commitment = next(
            (
                fixed
                for fixed in committed
                if overlaps(fixed.candidate, one.candidate, rules.turnaround_s)
            ),
            None,
        )
        if commitment is None:
            free.append(one)
        else:
            blocked.append(
                Rejection(
                    one,
                    "overlap",
                    commitment.candidate.pass_id,
                    committed_assignment_id=commitment.assignment_id,
                )
            )
    return free, blocked


def optimise(
    scored: Sequence[ScoredCandidate],
    *,
    rules: Rules,
    settings: SolverSettings,
    committed: Sequence[Commitment] = (),
) -> Optimised:
    """The valid selection of largest summed score, and how it was found.

    Args:
        scored: The candidates with their scores, in any order. A score is the
            candidate's value to the objective; the unit is the configuration's.
        rules: The turnaround and the delivery cap (D-166).
        settings: The time limit and the seed (D-167).
        committed: Assignments earlier runs made, fixed (D-165).

    Returns:
        A schedule that :func:`~meridian.scheduler.constraints.violations`
        accepts, and the :class:`SolverRun` that produced it.

    Raises:
        ValueError: A score is negative or not finite. A negative value would
            make leaving a pass out a gain, and the schedule would silently
            under-use the station.
    """
    for one in scored:
        if not math.isfinite(one.score) or one.score < 0:
            raise ValueError(
                f"pass {one.candidate.pass_id} scores {one.score}; "
                "a score is a finite, non-negative value"
            )
    free, blocked = _blocked_by_commitments(_canonical(scored), committed, rules)
    problem = Problem(
        candidates=tuple(one.candidate for one in scored),
        commitments=tuple(committed),
        unavailable=frozenset(),
        rules=rules,
    )
    answer = (
        solve(free, committed, rules, settings)
        if free
        else Answer(frozenset(), "optimal", 0.0, 0.0, None)
    )
    if answer.chosen is not None:
        outcome = _outcome(free, answer.chosen, blocked, committed, rules)
        found = violations(problem, outcome)
        if not found:
            return Optimised(outcome, _run(outcome, answer, settings))
        broken = sorted({one.rule for one in found})
        answer = Answer(
            None,
            "fallback",
            None,
            answer.runtime_s,
            f"the solver's schedule broke {', '.join(broken)}",
        )
    fallback = select_without_conflict(
        sorted(scored, key=_ranking_key), rules=rules, committed=committed
    )
    return Optimised(fallback, _run(fallback, answer, settings))


def _run(
    outcome: ScheduleOutcome, answer: Answer, settings: SolverSettings
) -> SolverRun:
    return SolverRun(
        status=answer.status,
        solver=SOLVER,
        version=solver_version(),
        objective=math.fsum(one.score for one in outcome.selected),
        bound=answer.bound,
        runtime_s=answer.runtime_s,
        time_limit_s=settings.time_limit_s,
        detail=answer.detail,
    )

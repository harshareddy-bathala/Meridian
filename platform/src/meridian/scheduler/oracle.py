"""The oracle: the best schedule the past allowed, knowing what every pass returned.

An upper bound for the retrospective comparison, and never a scheduler: it is
valued by outcomes, which exist only once the passes have flown, so nothing
deployed can call it. It is held to everything the schedulers it bounds are
held to — the same candidates, the same constraints, the same solver and the
same time limit (D-172) — so the distance from a scheduler to it is what
knowing the future is worth, and not a difference in physics.

**Each pass is worth the frames it decoded.** A pass that decoded nothing, or
was confirmed silent, is worth 0. So is a pass whose outcome is unknown, since
nobody attempted it: the oracle cannot know what it would have returned
either, and it values it at 0 rather than guess (rule 7). The comparison
counts how many such passes each schedule holds, the oracle's included.

Solved to optimality, it takes at least the frames of every schedule over the
same candidates: any of them is one of the selections it chose among.

Reference: docs/DECISIONS.md D-166, D-167, D-172; docs/EVALUATION.md §4.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.scheduler import Candidate, ScoredCandidate
from meridian.scheduler.constraints import Rules
from meridian.scheduler.optimiser import Optimised, SolverSettings, optimise

__all__ = ["oracle_scores", "schedule_oracle"]


def oracle_scores(
    candidates: Sequence[Candidate], frames: Mapping[int, int | None]
) -> list[ScoredCandidate]:
    """Each candidate valued by the frames it decoded, and 0 where unknown."""
    return [
        ScoredCandidate(candidate=one, score=float(frames.get(one.pass_id) or 0))
        for one in candidates
    ]


def schedule_oracle(
    candidates: Sequence[Candidate],
    frames: Mapping[int, int | None],
    *,
    rules: Rules,
    settings: SolverSettings,
) -> Optimised:
    """The selection of most decoded frames under D-166's constraints."""
    return optimise(oracle_scores(candidates, frames), rules=rules, settings=settings)

"""Configuration B — elevation weighted by what the operator cares about.

Configuration A ranks on geometry alone, which is a good heuristic and a poor
description of what a ground station operator actually does. Operators run
stations for a reason: one satellite is the project, another is a bonus. Existing
scheduling software has let them say so for years, through a per-satellite
priority.

**This is the baseline that counts.** docs/EVALUATION.md measures SC-1 as
**D − B**, not D − A, so this is the bar the shipped system has to clear —
reporting against elevation alone would take credit for priority weighting that
existing practice already has.

The score is elevation multiplied by priority. Multiplication rather than a sum
because the two quantities have no common unit: degrees plus a weight is a
number with no meaning, and its behaviour depends entirely on the arbitrary
scale of the weight. A multiplier is a statement about *relative* worth — a
priority of 2 says this satellite's passes are worth twice as much minute for
minute — and it is what "weighting" already means everywhere else.

A leaf: no I/O, no clock, no database.

Reference: docs/EVALUATION.md §3; docs/DECISIONS.md D-066.
"""

from __future__ import annotations

from collections.abc import Sequence

from meridian.scheduler import Candidate, ScoredCandidate

__all__ = [
    "MODEL_CONFIG",
    "NEUTRAL_PRIORITY",
    "priority_score",
    "rank_by_priority_weighted_elevation",
]

MODEL_CONFIG = "B"
"""Written to ``assignments.model_config`` on every decision this ranking makes.

Distinguishing B's rows from A's is what makes the two schedules comparable at
all, and the comparison is the evaluation (docs/EVALUATION.md §3).
"""

NEUTRAL_PRIORITY = 1.0
"""The weight at which this configuration reduces exactly to configuration A.

Not a coincidence and not a convention picked here — it is
``assignments.priority``'s column default, so a network where nobody has
expressed a preference ranks identically under A and B. That property is load
bearing: it means any measured difference between the two comes from priorities
an operator actually set, rather than from B using a different formula.
"""


def priority_score(candidate: Candidate) -> float:
    """Configuration B's score: culmination scaled by the operator's weight.

    Args:
        candidate: The pass being ranked.

    Returns:
        ``max_elevation_deg × priority``. Degrees when the priority is neutral,
        and a priority-weighted quantity otherwise — which is why
        ``assignments.score`` is unitless and read alongside ``model_config``.

    Raises:
        ValueError: ``priority`` is zero or negative. Zero makes every pass of
            that satellite tie at exactly zero whatever its geometry, so the
            order among them falls to the tie-break and the ranking silently
            stops being about elevation. Negative inverts it outright — the
            worst pass of that satellite would rank above the best. Neither has
            a defensible meaning as a multiplier, and both produce a schedule
            that looks entirely normal, which is precisely why this refuses
            rather than clamping.

    Note:
        A satellite an operator wants excluded is excluded by making it
        inactive, which takes it out of pass generation and out of the
        completeness denominator honestly (D-064). Expressing "never" as a
        priority of zero would instead fill the denominator with opportunities
        the scheduler was never going to take.
    """
    if candidate.priority <= 0:
        raise ValueError(
            f"priority must be positive, got {candidate.priority} "
            f"for pass {candidate.pass_id}"
        )

    return candidate.max_elevation_deg * candidate.priority


def rank_by_priority_weighted_elevation(
    candidates: Sequence[Candidate],
) -> list[ScoredCandidate]:
    """Score every candidate and order them best first.

    Args:
        candidates: The passes under consideration, in any order.

    Returns:
        Every candidate scored, highest first, with ties broken by earlier
        acquisition and then ``pass_id`` — the same total order
        ``elevation_baseline.rank_by_elevation`` uses, and for the same reason:
        a ranking that left ties to the input order would make the schedule
        depend on the order rows came back from the database.

    Raises:
        ValueError: Any candidate carries a non-positive priority.

    Note:
        The tie-break being identical to A's is what makes the two
        configurations comparable. If B broke ties differently, some of the
        measured D − B gap would be the tie-break rather than the model, and
        nothing in the reported number would separate them.
    """
    scored = [
        ScoredCandidate(candidate=candidate, score=priority_score(candidate))
        for candidate in candidates
    ]
    scored.sort(key=lambda one: (-one.score, one.candidate.aos, one.candidate.pass_id))
    return scored

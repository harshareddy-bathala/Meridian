"""Configuration A — rank candidate passes by how high the satellite gets.

The naive scheduler, and the one existing ground-station software effectively
implements: of the passes visible, prefer the ones that culminate highest. It is
a good heuristic. Elevation is the strongest single predictor of whether a pass
returns data — a high pass is closer, spends less signal in the atmosphere and
clears local obstructions — which is exactly why it is a baseline here rather
than something to argue against.

What it cannot do is the reason this project exists. It ranks on geometry alone,
so it cannot know that a station's north-east horizon is blocked by a building,
that its receiver runs hot in the afternoon, or that a 40-degree pass of a
satellite whose downlink is weak returns less than a 20-degree pass of one whose
downlink is strong. Configuration D learns those; the gap between them is the
measured claim in docs/EVALUATION.md.

A leaf: no I/O, no clock, no database. It takes candidates and returns them
scored, so a schedule can be checked against passes written out by hand.

Reference: docs/EVALUATION.md §3 (the four configurations); docs/DECISIONS.md
D-065.
"""

from __future__ import annotations

from collections.abc import Sequence

from meridian.scheduler import Candidate, ScoredCandidate

__all__ = [
    "MODEL_CONFIG",
    "elevation_score",
    "rank_by_elevation",
]

MODEL_CONFIG = "A"
"""Written to ``assignments.model_config`` on every decision this ranking makes.

docs/EVALUATION.md §3 names the four configurations A, B, C and D, and requires
any of them to be selectable by config flag. A stored schedule that does not say
which produced it cannot be compared against another, which is the entire
evaluation.
"""


def elevation_score(candidate: Candidate) -> float:
    """Configuration A's score: the candidate's culmination, in degrees.

    Args:
        candidate: The pass being ranked.

    Returns:
        Maximum elevation in degrees. Higher is better, and the number is
        returned unchanged rather than normalised — a score of 47.2 read off a
        stored assignment is then directly the elevation that produced it, which
        is what makes a decision explainable years later without the code that
        made it.
    """
    return candidate.max_elevation_deg


def rank_by_elevation(candidates: Sequence[Candidate]) -> list[ScoredCandidate]:
    """Score every candidate and order them best first.

    Args:
        candidates: The passes under consideration, in any order.

    Returns:
        Every candidate scored, highest elevation first. Ties are broken by
        earlier acquisition, then by ``pass_id`` — a total order, so shuffling
        the input cannot change the output.

    Note:
        **The tie-break is part of the algorithm, not tidiness.** Two passes
        culminating at the same elevation is not a rare edge case: a station
        tracking one satellite sees near-identical geometry on consecutive days,
        and simulated stations sharing a seed produce exact ties by
        construction. A ranking that left those to the input order would make
        the schedule depend on the order rows came back from the database, and
        every number computed from it would stop being reproducible — which
        CLAUDE.md requires each one to be.

        Earlier acquisition wins first because a pass in hand is worth more than
        an identical one later: less time for the station to go offline, for the
        element set to age, or for an operator to intervene. ``pass_id`` then
        settles what remains, since it is unique among candidates in a run.
    """
    scored = [
        ScoredCandidate(candidate=candidate, score=elevation_score(candidate))
        for candidate in candidates
    ]
    scored.sort(key=lambda one: (-one.score, one.candidate.aos, one.candidate.pass_id))
    return scored

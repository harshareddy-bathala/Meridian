"""Configuration B's ranking, and its relationship to configuration A.

The property that matters most here is not any single ordering — it is that B
reduces exactly to A when nobody has expressed a preference. That is what makes
docs/EVALUATION.md's D − B a measurement of priority weighting rather than of a
formula change, so it is asserted directly rather than assumed.

Marked as a unit test by living in ``tests/unit``: no database, no propagator.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from meridian.scheduler import Candidate
from meridian.scheduler.elevation_baseline import rank_by_elevation
from meridian.scheduler.priority_baseline import (
    MODEL_CONFIG,
    NEUTRAL_PRIORITY,
    priority_score,
    rank_by_priority_weighted_elevation,
)

T0 = datetime(2026, 8, 14, tzinfo=UTC)
"""An arbitrary anchor. Every candidate below is written in minutes from here."""


def a_candidate(
    pass_id: int,
    *,
    max_elevation_deg: float,
    priority: float = NEUTRAL_PRIORITY,
    at_minute: float = 0.0,
) -> Candidate:
    """One candidate pass, eleven minutes long, starting at ``at_minute``."""
    aos = T0 + timedelta(minutes=at_minute)
    return Candidate(
        pass_id=pass_id,
        station_id="st_001",
        aos=aos,
        los=aos + timedelta(minutes=11),
        max_elevation_deg=max_elevation_deg,
        priority=priority,
        simulated=False,
    )


def ranked_ids(candidates: list[Candidate]) -> list[int]:
    """The pass ids in the order configuration B put them."""
    return [
        one.candidate.pass_id for one in rank_by_priority_weighted_elevation(candidates)
    ]


def test_the_score_is_elevation_times_priority() -> None:
    """Written out from the two numbers, not read back from the function."""
    assert priority_score(a_candidate(1, max_elevation_deg=30.0, priority=2.0)) == 60.0


def test_a_neutral_priority_leaves_the_elevation_unchanged() -> None:
    """The column default is 1.0, so an unweighted network scores in degrees."""
    candidate = a_candidate(1, max_elevation_deg=42.0, priority=NEUTRAL_PRIORITY)
    assert priority_score(candidate) == 42.0


def test_priority_can_lift_a_lower_pass_above_a_higher_one() -> None:
    """The whole point of configuration B.

    A 30-degree pass of a satellite worth double beats a 50-degree pass of one
    worth single: 60 against 50. Under configuration A the order is reversed,
    and that difference is what B exists to express.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=50.0, priority=1.0, at_minute=0),
        a_candidate(2, max_elevation_deg=30.0, priority=2.0, at_minute=100),
    ]

    assert ranked_ids(candidates) == [2, 1]
    assert [one.candidate.pass_id for one in rank_by_elevation(candidates)] == [1, 2]


def test_priority_does_not_override_geometry_outright() -> None:
    """A weight is a multiplier, not a precedence class.

    A priority-2 satellite at 10 degrees scores 20 and still loses to a
    priority-1 pass at 50. A lexicographic rule — priority first, elevation only
    as a tie-break — would schedule the 10-degree pass, and a station would
    spend its slot on geometry that mostly does not decode.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=50.0, priority=1.0, at_minute=0),
        a_candidate(2, max_elevation_deg=10.0, priority=2.0, at_minute=100),
    ]
    assert ranked_ids(candidates) == [1, 2]


def test_configuration_b_reduces_exactly_to_configuration_a() -> None:
    """The property that makes D − B a measurement of priority weighting.

    With every priority at the column default, B must produce A's order
    candidate for candidate — not merely a similar one. If it did not, part of
    any measured gap between the configurations would be the formula rather than
    the feature, and nothing in the published number would separate the two.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=12.0, at_minute=0),
        a_candidate(2, max_elevation_deg=78.0, at_minute=100),
        a_candidate(3, max_elevation_deg=31.0, at_minute=200),
        a_candidate(4, max_elevation_deg=78.0, at_minute=300),
    ]

    by_a = rank_by_elevation(candidates)
    by_b = rank_by_priority_weighted_elevation(candidates)

    assert [one.candidate.pass_id for one in by_b] == [
        one.candidate.pass_id for one in by_a
    ]
    assert [one.score for one in by_b] == [one.score for one in by_a]


def test_ties_break_the_same_way_configuration_a_breaks_them() -> None:
    """Earlier acquisition, then pass id — identical to A by design.

    A different tie-break would put part of the D − B gap down to tie handling,
    which is not a feature and would not be visible in the reported number.
    """
    candidates = [
        a_candidate(9, max_elevation_deg=20.0, priority=2.0, at_minute=500),
        a_candidate(4, max_elevation_deg=40.0, priority=1.0, at_minute=100),
        a_candidate(7, max_elevation_deg=40.0, priority=1.0, at_minute=100),
    ]
    assert ranked_ids(candidates) == [4, 7, 9]


def test_shuffling_the_input_cannot_change_the_output() -> None:
    """Reproducibility, asserted the same way as for configuration A."""
    candidates = [
        a_candidate(1, max_elevation_deg=40.0, priority=1.5, at_minute=0),
        a_candidate(2, max_elevation_deg=60.0, priority=1.0, at_minute=0),
        a_candidate(3, max_elevation_deg=30.0, priority=2.0, at_minute=60),
        a_candidate(4, max_elevation_deg=20.0, priority=3.0, at_minute=120),
    ]
    expected = ranked_ids(candidates)

    shuffler = random.Random(4471)
    for _ in range(20):
        shuffled = candidates[:]
        shuffler.shuffle(shuffled)
        assert ranked_ids(shuffled) == expected


def test_a_zero_priority_is_refused() -> None:
    """Every pass of that satellite would tie at zero whatever its geometry.

    The ranking would silently stop being about elevation for those passes and
    fall through to the tie-break, and the resulting schedule would look
    entirely normal. A satellite an operator wants excluded is made inactive,
    which takes it out of the completeness denominator honestly (D-064).
    """
    with pytest.raises(ValueError, match="positive"):
        priority_score(a_candidate(1, max_elevation_deg=40.0, priority=0.0))


def test_a_negative_priority_is_refused() -> None:
    """It inverts the ranking: the worst pass of that satellite would rank
    above the best, and nothing about the output would look wrong."""
    with pytest.raises(ValueError, match="positive"):
        priority_score(a_candidate(1, max_elevation_deg=40.0, priority=-1.0))


def test_one_bad_priority_fails_the_whole_ranking() -> None:
    """Not skipped, not scored as zero — a schedule built from data the operator
    got wrong is worse than no schedule, because it is indistinguishable from
    one built from data they got right."""
    candidates = [
        a_candidate(1, max_elevation_deg=40.0, priority=1.0),
        a_candidate(2, max_elevation_deg=40.0, priority=-2.0, at_minute=100),
    ]
    with pytest.raises(ValueError, match="pass 2"):
        rank_by_priority_weighted_elevation(candidates)


def test_the_configuration_is_named_so_a_schedule_can_be_attributed() -> None:
    """EVALUATION.md's SC-1 is D − B, so B's rows must be identifiable."""
    assert MODEL_CONFIG == "B"

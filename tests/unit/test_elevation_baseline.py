"""Configuration A's ranking, against candidates written out by hand.

Every expected order below is read off the elevations in the test itself, not
computed by the code under test. The module is a leaf with no I/O, so these need
no database, no propagator and no element set.

Marked as a unit test by living in ``tests/unit``.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from meridian.scheduler import Candidate
from meridian.scheduler.elevation_baseline import (
    MODEL_CONFIG,
    elevation_score,
    rank_by_elevation,
)

T0 = datetime(2026, 8, 14, tzinfo=UTC)
"""An arbitrary anchor. Every candidate below is written in minutes from here."""

STATION = "st_001"


def a_candidate(
    pass_id: int,
    *,
    max_elevation_deg: float,
    at_minute: float = 0.0,
    priority: float = 1.0,
    station_id: str = STATION,
) -> Candidate:
    """One candidate pass, eleven minutes long, starting at ``at_minute``."""
    aos = T0 + timedelta(minutes=at_minute)
    return Candidate(
        pass_id=pass_id,
        station_id=station_id,
        aos=aos,
        los=aos + timedelta(minutes=11),
        max_elevation_deg=max_elevation_deg,
        priority=priority,
        simulated=False,
    )


def ranked_ids(candidates: list[Candidate]) -> list[int]:
    """The pass ids in the order the ranking put them."""
    return [one.candidate.pass_id for one in rank_by_elevation(candidates)]


def test_the_score_is_the_culmination_in_degrees_unchanged() -> None:
    """Not normalised, so a stored score is directly the elevation behind it."""
    assert elevation_score(a_candidate(1, max_elevation_deg=47.2)) == 47.2


def test_the_highest_pass_ranks_first() -> None:
    candidates = [
        a_candidate(1, max_elevation_deg=12.0, at_minute=0),
        a_candidate(2, max_elevation_deg=78.0, at_minute=100),
        a_candidate(3, max_elevation_deg=31.0, at_minute=200),
    ]
    assert ranked_ids(candidates) == [2, 3, 1]


def test_every_candidate_comes_back_scored() -> None:
    """Ranking selects nothing — dropping a low pass here would remove it from
    the schedule without any rejection recording that it was considered."""
    candidates = [
        a_candidate(1, max_elevation_deg=5.0),
        a_candidate(2, max_elevation_deg=85.0, at_minute=100),
    ]

    scored = rank_by_elevation(candidates)

    assert len(scored) == 2
    assert {one.candidate.pass_id for one in scored} == {1, 2}
    assert [one.score for one in scored] == [85.0, 5.0]


def test_an_empty_list_ranks_to_an_empty_list() -> None:
    assert rank_by_elevation([]) == []


def test_ties_are_broken_by_earlier_acquisition() -> None:
    """A pass in hand beats an identical one later.

    Less time for the station to go offline, for the element set to age, or for
    an operator to intervene.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=40.0, at_minute=500),
        a_candidate(2, max_elevation_deg=40.0, at_minute=100),
    ]
    assert ranked_ids(candidates) == [2, 1]


def test_a_tie_on_elevation_and_time_is_broken_by_pass_id() -> None:
    """The last resort, so the order is total rather than merely usually total.

    Two predictions of the same physical pass from different element sets
    (D-063) genuinely can share an acquisition to the microsecond and a
    culmination to the degree.
    """
    candidates = [
        a_candidate(9, max_elevation_deg=40.0, at_minute=100),
        a_candidate(4, max_elevation_deg=40.0, at_minute=100),
    ]
    assert ranked_ids(candidates) == [4, 9]


def test_shuffling_the_input_cannot_change_the_output() -> None:
    """The property the tie-break exists for, asserted directly.

    Exact ties are not exotic here: a station tracking one satellite sees
    near-identical geometry on consecutive days, and simulated stations sharing
    a seed produce ties by construction. Left to input order, the schedule would
    depend on the order rows came back from the database and every number
    computed from it would stop being reproducible.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=40.0, at_minute=0),
        a_candidate(2, max_elevation_deg=40.0, at_minute=0),
        a_candidate(3, max_elevation_deg=40.0, at_minute=60),
        a_candidate(4, max_elevation_deg=75.0, at_minute=120),
        a_candidate(5, max_elevation_deg=75.0, at_minute=120),
    ]
    expected = ranked_ids(candidates)

    shuffler = random.Random(4471)
    for _ in range(20):
        shuffled = candidates[:]
        shuffler.shuffle(shuffled)
        assert ranked_ids(shuffled) == expected


def test_priority_is_ignored_by_configuration_a() -> None:
    """A is elevation *only*. Folding priority in here would make it B, and
    docs/EVALUATION.md's SC-1 is measured as D − B precisely so the project does
    not take credit for priority weighting that existing practice already has.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=20.0, at_minute=0, priority=99.0),
        a_candidate(2, max_elevation_deg=60.0, at_minute=100, priority=0.1),
    ]
    assert ranked_ids(candidates) == [2, 1]


def test_candidates_for_different_stations_rank_in_one_list() -> None:
    """Ranking is global; keeping stations apart is the conflict rule's job.

    A station's own antenna is what a pass competes for, and that constraint is
    applied in ``conflict_rejection``. Ranking per station here would quietly
    fix the shape of the eventual optimiser, which allocates across the network.
    """
    candidates = [
        a_candidate(1, max_elevation_deg=30.0, station_id="st_a"),
        a_candidate(2, max_elevation_deg=70.0, station_id="st_b"),
    ]
    assert ranked_ids(candidates) == [2, 1]


def test_the_configuration_is_named_so_a_schedule_can_be_attributed() -> None:
    """A stored schedule that does not say which configuration produced it
    cannot be compared against another, and the comparison is the evaluation."""
    assert MODEL_CONFIG == "A"

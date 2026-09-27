"""Greedy non-overlap, against windows written out by hand.

Every overlap below is arithmetic in minutes from one anchor, so a reader can
check which passes collide without running anything. The module is a leaf with
no I/O, so these need no database and no propagator.

Marked as a unit test by living in ``tests/unit``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.scheduler import Candidate, Commitment, ScoredCandidate
from meridian.scheduler.assignment_records import (
    PassFacts,
    assignment_id_for,
    to_assignment_rows,
)
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import Rules, overlaps

T0 = datetime(2026, 8, 14, tzinfo=UTC)
"""An arbitrary anchor. Every window below is written in minutes from here."""

STATION = "st_001"
OTHER_STATION = "st_002"

PASS_MINUTES = 11.0
"""A realistic low Earth orbit pass — CLAUDE.md puts them at 8 to 15 minutes."""

NO_TURNAROUND_S = 0.0
"""A fixed-antenna station: nothing to slew, so passes may abut exactly."""

ROTATOR_TURNAROUND_S = 90.0
"""A tracking station needing a minute and a half to swing round and settle.

An illustrative figure for these tests, not the platform's answer — the value a
real run uses is the scheduler run's parameter, and it is not decided here.
"""


def a_candidate(
    pass_id: int,
    *,
    at_minute: float,
    length_minutes: float = PASS_MINUTES,
    station_id: str = STATION,
) -> Candidate:
    """One candidate pass starting at ``at_minute`` from the anchor."""
    aos = T0 + timedelta(minutes=at_minute)
    return Candidate(
        pass_id=pass_id,
        station_id=station_id,
        aos=aos,
        los=aos + timedelta(minutes=length_minutes),
        margin_s=0.0,
        max_elevation_deg=45.0,
        priority=1.0,
        simulated=False,
    )


def ranked(*candidates: Candidate) -> list[ScoredCandidate]:
    """Treat the given order as the ranking, best first.

    Scores descend so the list is self-consistently ordered; the rule under test
    obeys the order it is given and never re-derives it.
    """
    return [
        ScoredCandidate(candidate=candidate, score=float(100 - index))
        for index, candidate in enumerate(candidates)
    ]


# --- the pairwise rule --------------------------------------------------------


def test_two_overlapping_passes_for_one_station_conflict() -> None:
    """Overlapping by ten of their eleven minutes."""
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=1)
    assert overlaps(first, second, NO_TURNAROUND_S) is True


def test_an_overlap_of_one_second_is_still_a_conflict() -> None:
    """One antenna cannot be in two places, however brief the collision."""
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=PASS_MINUTES - 1.0 / 60.0)
    assert overlaps(first, second, NO_TURNAROUND_S) is True


def test_passes_touching_at_a_boundary_do_not_conflict() -> None:
    """With no slew to perform there is nothing in the way.

    Not an edge case to shrug at: passes of one satellite from one station recur
    on a near-fixed period, so near-abutting windows are common. Resolving this
    the other way would silently discard a pass at every seam.
    """
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=PASS_MINUTES)
    assert overlaps(first, second, NO_TURNAROUND_S) is False


def test_the_conflict_test_does_not_depend_on_argument_order() -> None:
    """Overlap is symmetric, and a greedy walk compares in both directions."""
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=5)

    assert overlaps(first, second, NO_TURNAROUND_S) is True
    assert overlaps(second, first, NO_TURNAROUND_S) is True


def test_two_stations_never_conflict_however_much_they_overlap() -> None:
    """The constraint is one antenna, not one network.

    Two stations receiving at once is the entire point of a network, and a rule
    that serialised them would make every station after the first useless.
    """
    here = a_candidate(1, at_minute=0)
    elsewhere = a_candidate(2, at_minute=0, station_id=OTHER_STATION)
    assert overlaps(here, elsewhere, NO_TURNAROUND_S) is False


def test_turnaround_makes_abutting_passes_conflict() -> None:
    """A station that has to slew cannot start the next pass as the last ends.

    The same two windows that are compatible for a fixed antenna are not for a
    rotator, which is why turnaround is an input rather than a constant.
    """
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=PASS_MINUTES)

    assert overlaps(first, second, ROTATOR_TURNAROUND_S) is True


def test_a_gap_wider_than_the_turnaround_is_still_free() -> None:
    """Two minutes apart, with ninety seconds of slew to do."""
    first = a_candidate(1, at_minute=0)
    second = a_candidate(2, at_minute=PASS_MINUTES + 2.0)

    assert overlaps(first, second, ROTATOR_TURNAROUND_S) is False


# --- the greedy walk ----------------------------------------------------------


def test_the_best_ranked_candidate_is_always_taken() -> None:
    """Nothing is selected before it, so nothing can block it."""
    outcome = select_without_conflict(
        ranked(a_candidate(1, at_minute=0), a_candidate(2, at_minute=1)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [1]
    assert [one.scored.candidate.pass_id for one in outcome.rejected] == [2]


def test_a_rejection_names_the_selection_that_displaced_it() -> None:
    """PROJECT.md §13's screen needs the winner, not just the fact of a loss."""
    outcome = select_without_conflict(
        ranked(a_candidate(7, at_minute=0), a_candidate(8, at_minute=3)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert outcome.rejected[0].conflicts_with_pass_id == 7


def test_a_rejection_is_blamed_on_the_best_ranked_blocker() -> None:
    """Two selections both block it; the one an operator is owed is the better.

    Candidates 1 and 2 do not collide with each other and are both taken.
    Candidate 3 spans both. Naming whichever happened to be compared first would
    make the explanation depend on iteration order rather than on rank.
    """
    outcome = select_without_conflict(
        ranked(
            a_candidate(1, at_minute=0),
            a_candidate(2, at_minute=20),
            a_candidate(3, at_minute=5, length_minutes=20),
        ),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [1, 2]
    assert outcome.rejected[0].conflicts_with_pass_id == 1


def test_a_pass_freed_by_a_rejection_is_still_taken() -> None:
    """A rejected candidate blocks nothing — only selections do.

    Candidate 2 loses to 1. Candidate 3 overlaps 2 but not 1, so it must be
    taken: treating rejects as occupying the slot they lost would cascade one
    conflict into an empty schedule.
    """
    outcome = select_without_conflict(
        ranked(
            a_candidate(1, at_minute=0),
            a_candidate(2, at_minute=8),
            a_candidate(3, at_minute=13),
        ),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [1, 3]
    assert [one.scored.candidate.pass_id for one in outcome.rejected] == [2]


def test_two_stations_are_scheduled_independently() -> None:
    """Overlapping passes at two stations are both taken."""
    outcome = select_without_conflict(
        ranked(
            a_candidate(1, at_minute=0),
            a_candidate(2, at_minute=0, station_id=OTHER_STATION),
        ),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [1, 2]
    assert outcome.rejected == []


def test_the_ranking_order_is_obeyed_and_never_re_derived() -> None:
    """The lower-elevation pass wins when the ranking puts it first.

    One non-overlap rule serves every configuration in EVALUATION.md §3, which
    only holds if this function has no opinion about what is better. Here the
    ranking is deliberately at odds with elevation to prove it has none.
    """
    worse_geometry = a_candidate(1, at_minute=0)
    better_geometry = a_candidate(2, at_minute=3)

    outcome = select_without_conflict(
        [
            ScoredCandidate(candidate=worse_geometry, score=1.0),
            ScoredCandidate(candidate=better_geometry, score=99.0),
        ],
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [1]


def test_every_candidate_appears_in_exactly_one_output_list() -> None:
    """A dropped candidate is a pass the platform can no longer account for.

    It would be absent from the schedule and absent from the rejections, so the
    screen explaining the schedule would have a hole in it with nothing to
    detect one.
    """
    candidates = [a_candidate(number, at_minute=number * 4) for number in range(1, 9)]

    outcome = select_without_conflict(
        ranked(*candidates), rules=Rules(turnaround_s=60.0)
    )

    accounted = [one.candidate.pass_id for one in outcome.selected]
    accounted += [one.scored.candidate.pass_id for one in outcome.rejected]
    assert sorted(accounted) == [one.pass_id for one in candidates]


def test_no_two_selections_conflict_with_each_other() -> None:
    """The property the whole function exists to guarantee, asserted directly.

    Checked pairwise over the result rather than inferred from the count, so a
    walk that admitted a collision fails here even where it selected a plausible
    number of passes.
    """
    candidates = [
        a_candidate(number, at_minute=number * 2.5) for number in range(1, 13)
    ]

    outcome = select_without_conflict(
        ranked(*candidates), rules=Rules(turnaround_s=ROTATOR_TURNAROUND_S)
    )

    taken = [one.candidate for one in outcome.selected]
    assert len(taken) > 1
    for index, one in enumerate(taken):
        for other in taken[index + 1 :]:
            assert overlaps(one, other, ROTATOR_TURNAROUND_S) is False


def test_an_empty_candidate_list_schedules_nothing() -> None:
    outcome = select_without_conflict([], rules=Rules(turnaround_s=NO_TURNAROUND_S))

    assert outcome.selected == []
    assert outcome.rejected == []


def test_a_negative_turnaround_is_refused() -> None:
    """It would let two genuinely overlapping passes be scheduled together."""
    with pytest.raises(ValueError, match="turnaround_s"):
        Rules(turnaround_s=-1.0)


# --- commitments: what earlier runs already assigned (D-165) ------------------


def committed(
    pass_id: int, *, at_minute: float, station_id: str = STATION
) -> Commitment:
    """An assignment an earlier run made, under whatever configuration."""
    return Commitment(
        candidate=a_candidate(pass_id, at_minute=at_minute, station_id=station_id),
        assignment_id=f"as_earlier_{pass_id}",
    )


def test_a_newcomer_cannot_displace_a_commitment_however_it_ranks() -> None:
    """The overlapping-rounds defect, in miniature.

    Pass 2 is new to this round and ranks first. Before D-165 the round saw
    only its own candidates, took it, and left the earlier assignment standing
    beside it: two overlapping assignments for one antenna.
    """
    outcome = select_without_conflict(
        ranked(a_candidate(2, at_minute=5)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
        committed=[committed(1, at_minute=0)],
    )

    assert outcome.selected == []
    assert [one.committed_assignment_id for one in outcome.rejected] == ["as_earlier_1"]
    assert outcome.rejected[0].conflicts_with_pass_id == 1


def test_a_commitment_is_never_part_of_the_outcome() -> None:
    """It was decided earlier; deciding it again would write it twice."""
    outcome = select_without_conflict(
        ranked(a_candidate(2, at_minute=30)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
        committed=[committed(1, at_minute=0)],
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [2]
    assert outcome.rejected == []


def test_a_commitment_binds_only_its_own_station() -> None:
    outcome = select_without_conflict(
        ranked(a_candidate(2, at_minute=5)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
        committed=[committed(1, at_minute=0, station_id=OTHER_STATION)],
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [2]


def test_a_commitment_honours_the_turnaround() -> None:
    """Abutting a commitment is fine for a fixed antenna and not for a rotator."""
    candidate = ranked(a_candidate(2, at_minute=PASS_MINUTES))
    earlier = [committed(1, at_minute=0)]

    fixed = select_without_conflict(
        candidate, rules=Rules(turnaround_s=NO_TURNAROUND_S), committed=earlier
    )
    rotator = select_without_conflict(
        candidate, rules=Rules(turnaround_s=ROTATOR_TURNAROUND_S), committed=earlier
    )

    assert len(fixed.selected) == 1
    assert len(rotator.rejected) == 1


def test_a_commitment_is_named_before_a_selection_of_this_run() -> None:
    """Pass 3 collides with both; the assignment that already exists is named."""
    outcome = select_without_conflict(
        ranked(a_candidate(2, at_minute=12), a_candidate(3, at_minute=8)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
        committed=[committed(1, at_minute=0)],
    )

    assert [one.candidate.pass_id for one in outcome.selected] == [2]
    assert [
        (one.scored.candidate.pass_id, one.committed_assignment_id)
        for one in outcome.rejected
    ] == [(3, "as_earlier_1")]


def test_a_skip_behind_a_commitment_names_that_assignment_in_its_row() -> None:
    """Not an id minted from the pass: the commitment may be another
    configuration's, and its id is the one that exists."""
    outcome = select_without_conflict(
        ranked(a_candidate(2, at_minute=5), a_candidate(3, at_minute=40)),
        rules=Rules(turnaround_s=NO_TURNAROUND_S),
        committed=[committed(1, at_minute=0)],
    )
    facts = {
        pass_id: PassFacts(
            centre_freq_hz=137_100_000,
            mode="lrpt",
            aos=T0,
            los=T0 + timedelta(minutes=PASS_MINUTES),
            timing_uncertainty_s=0.5,
        )
        for pass_id in (2, 3)
    }

    rows = {row.pass_id: row for row in to_assignment_rows(outcome, facts, "B")}

    assert rows[2].conflicts_with_assignment_id == "as_earlier_1"
    assert rows[2].conflicts_with_assignment_id != assignment_id_for(1, "B")
    assert rows[3].conflicts_with_assignment_id is None

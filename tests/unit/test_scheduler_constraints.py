"""The constraints every schedule obeys, and the check that one does (D-166).

Windows are written in minutes from one anchor, so each collision can be checked
by hand. The validator is tested two ways: every greedy schedule over seeded
random instances passes it, and a schedule broken by hand in each way it can be
broken fails it, naming the rule.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meridian.api.models.assignment import MAX_ASSIGNMENTS_PER_RESPONSE
from meridian.api.msp.heartbeat import ASSIGNMENT_HORIZON
from meridian.scheduler import (
    Candidate,
    Commitment,
    ScheduleOutcome,
    ScoredCandidate,
)
from meridian.scheduler.assignment_records import PassFacts, to_assignment_rows
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import (
    DELIVERY_LEAD,
    MOST_ELIGIBLE,
    Problem,
    Rules,
    exceeds_cap,
    overlaps,
    violations,
    window,
)

T0 = datetime(2026, 9, 27, tzinfo=UTC)
STATION = "st_001"
OTHER = "st_002"
FIXED = Rules(turnaround_s=0.0)


def a_pass(
    pass_id: int,
    *,
    at_minute: float,
    minutes: float = 11.0,
    margin_s: float = 0.0,
    station_id: str = STATION,
) -> Candidate:
    aos = T0 + timedelta(minutes=at_minute)
    return Candidate(
        pass_id=pass_id,
        station_id=station_id,
        aos=aos,
        los=aos + timedelta(minutes=minutes),
        margin_s=margin_s,
        max_elevation_deg=45.0,
        priority=1.0,
        simulated=False,
    )


def ranked(*candidates: Candidate) -> list[ScoredCandidate]:
    """The given order is the ranking, best first."""
    return [
        ScoredCandidate(candidate=one, score=float(100 - index))
        for index, one in enumerate(candidates)
    ]


def a_problem(
    candidates: list[Candidate],
    *,
    commitments: tuple[Commitment, ...] = (),
    unavailable: frozenset[str] = frozenset(),
    rules: Rules = FIXED,
) -> Problem:
    return Problem(
        candidates=tuple(candidates),
        commitments=commitments,
        unavailable=unavailable,
        rules=rules,
    )


def greedy(problem: Problem) -> ScheduleOutcome:
    return select_without_conflict(
        ranked(*problem.candidates),
        rules=problem.rules,
        committed=problem.commitments,
    )


# --- the delivery constants are the protocol's -------------------------------


def test_the_cap_is_what_one_heartbeat_delivers() -> None:
    """D-035: the scheduler enforces the delivery cap, so they are one number."""
    assert MOST_ELIGIBLE == MAX_ASSIGNMENTS_PER_RESPONSE
    assert DELIVERY_LEAD == ASSIGNMENT_HORIZON


def test_rules_refuse_a_cap_below_one() -> None:
    with pytest.raises(ValueError, match="most_eligible"):
        Rules(turnaround_s=0.0, most_eligible=0)


# --- one antenna, judged on the assignment window -----------------------------


def test_the_window_is_the_pass_opened_out_by_its_margin() -> None:
    one = a_pass(1, at_minute=0, margin_s=30.0)

    assert window(one) == (T0 - timedelta(seconds=30), one.los + timedelta(seconds=30))


def test_passes_that_abut_conflict_once_their_windows_are_widened() -> None:
    """A station recording one pass to its widened end cannot start the next
    at its widened start. On the passes alone they would only touch."""
    first = a_pass(1, at_minute=0)
    second = a_pass(2, at_minute=11)

    assert overlaps(first, second, 0.0) is False
    assert overlaps(replace(first, margin_s=0.5), replace(second, margin_s=0.5), 0.0)


def test_a_margin_on_one_side_is_enough() -> None:
    first = a_pass(1, at_minute=0, margin_s=1.0)
    second = a_pass(2, at_minute=11)

    assert overlaps(first, second, 0.0)
    assert overlaps(second, first, 0.0)


# --- the delivery cap ---------------------------------------------------------


def nine_short_passes(station_id: str = STATION) -> list[Candidate]:
    """Nine non-overlapping five-minute passes inside one hour."""
    return [
        a_pass(number, at_minute=number * 6, minutes=5, station_id=station_id)
        for number in range(1, 10)
    ]


def test_greedy_takes_eight_eligible_at_once_and_skips_the_ninth() -> None:
    """All nine are eligible together from the first one's end, two hours
    before the last one starts, so one heartbeat would have to carry nine."""
    outcome = greedy(a_problem(nine_short_passes()))

    assert [one.candidate.pass_id for one in outcome.selected] == list(range(1, 9))
    (rejection,) = outcome.rejected
    assert (rejection.rule, rejection.conflicts_with_pass_id) == ("eligible_cap", None)


def test_the_cap_is_per_station() -> None:
    """Eight here and one elsewhere is nobody's ninth."""
    candidates = [
        *nine_short_passes()[:8],
        a_pass(99, at_minute=48, minutes=5, station_id=OTHER),
    ]

    outcome = greedy(a_problem(candidates))

    assert outcome.rejected == []


def test_passes_further_apart_than_the_lead_are_never_eligible_together() -> None:
    """Spread over a day, nothing is eligible at once, so nothing is capped."""
    candidates = [
        a_pass(number, at_minute=number * 180, minutes=5) for number in range(1, 13)
    ]

    assert greedy(a_problem(candidates)).rejected == []


def test_eligibility_that_only_touches_still_counts_together() -> None:
    """At the instant one assignment's end and another's lead-in meet, a
    heartbeat would carry both, so a cap of one refuses the second."""
    first = a_pass(1, at_minute=0, minutes=5)
    touching = a_pass(2, at_minute=5 + 120, minutes=5)
    rules = Rules(turnaround_s=0.0, most_eligible=1)

    assert exceeds_cap(touching, [first], rules)
    assert not exceeds_cap(
        replace(touching, aos=touching.aos + timedelta(seconds=1)), [first], rules
    )


def test_commitments_count_towards_the_cap() -> None:
    passes = nine_short_passes()
    commitments = tuple(
        Commitment(candidate=one, assignment_id=f"as_{one.pass_id}")
        for one in passes[:8]
    )

    outcome = greedy(a_problem([passes[8]], commitments=commitments))

    assert [one.rule for one in outcome.rejected] == ["eligible_cap"]


def test_a_cap_rejection_is_written_without_a_winner() -> None:
    """No single assignment took the slot, so the row names none and says why."""
    passes = nine_short_passes()
    outcome = greedy(a_problem(passes))
    facts = {
        one.pass_id: PassFacts(
            centre_freq_hz=137_100_000,
            mode="lrpt",
            aos=one.aos,
            los=one.los,
            timing_uncertainty_s=0.0,
        )
        for one in passes
    }

    rows = {row.pass_id: row for row in to_assignment_rows(outcome, facts, "A")}

    assert rows[9].decision == "skipped"
    assert rows[9].conflicts_with_assignment_id is None
    assert "D-035" in rows[9].reason


# --- the validator: a clean schedule passes -----------------------------------


def random_problem(seed: int) -> Problem:
    """Two stations, up to twenty passes and a few commitments, all seeded."""
    rng = random.Random(seed)
    candidates = []
    for pass_id in range(1, rng.randint(1, 20) + 1):
        candidates.append(
            a_pass(
                pass_id,
                at_minute=rng.uniform(0, 360),
                minutes=rng.uniform(4, 15),
                margin_s=rng.choice([0.0, 0.5, 3.0, 60.0]),
                station_id=rng.choice([STATION, OTHER]),
            )
        )
    commitments = tuple(
        Commitment(
            candidate=a_pass(
                1000 + number,
                at_minute=rng.uniform(-60, 400),
                minutes=rng.uniform(4, 15),
                station_id=rng.choice([STATION, OTHER]),
            ),
            assignment_id=f"as_c{number}",
        )
        for number in range(rng.randint(0, 3))
    )
    rules = Rules(
        turnaround_s=rng.choice([0.0, 90.0]), most_eligible=rng.choice([2, 3, 8])
    )
    return a_problem(candidates, commitments=commitments, rules=rules)


@pytest.mark.parametrize("seed", range(300))
def test_every_greedy_schedule_passes_the_validator(seed: int) -> None:
    problem = random_problem(seed)

    assert violations(problem, greedy(problem)) == ()


def test_the_random_instances_exercise_every_rejection_rule() -> None:
    """Positive control: the property above is not passing on easy instances."""
    rules = {
        rejection.rule
        for seed in range(300)
        for rejection in greedy(random_problem(seed)).rejected
    }
    committed_blocks = sum(
        rejection.committed_assignment_id is not None
        for seed in range(300)
        for rejection in greedy(random_problem(seed)).rejected
    )

    assert rules == {"overlap", "eligible_cap"}
    assert committed_blocks > 0


# --- the validator: a schedule broken by hand fails, naming the rule ----------


def rules_broken(problem: Problem, outcome: ScheduleOutcome) -> set[str]:
    return {one.rule for one in violations(problem, outcome)}


def test_an_overlap_is_caught() -> None:
    problem = a_problem([a_pass(1, at_minute=0), a_pass(2, at_minute=5)])
    outcome = greedy(problem)
    forced = ScheduleOutcome(
        selected=[*outcome.selected, outcome.rejected[0].scored], rejected=[]
    )

    assert rules_broken(problem, forced) == {"overlap"}


def test_an_overlap_with_a_commitment_is_caught() -> None:
    commitment = Commitment(candidate=a_pass(9, at_minute=0), assignment_id="as_9")
    problem = a_problem([a_pass(1, at_minute=5)], commitments=(commitment,))
    forced = ScheduleOutcome(selected=ranked(a_pass(1, at_minute=5)), rejected=[])

    assert rules_broken(problem, forced) == {"overlap"}


def test_a_breach_of_the_cap_is_caught() -> None:
    passes = nine_short_passes()
    problem = a_problem(passes)

    forced = ScheduleOutcome(selected=ranked(*passes), rejected=[])

    assert rules_broken(problem, forced) == {"eligible_cap"}


def test_an_assignment_to_an_offline_station_is_caught() -> None:
    problem = a_problem([a_pass(1, at_minute=0)], unavailable=frozenset({STATION}))

    assert rules_broken(problem, greedy(problem)) == {"unavailable"}


def test_a_candidate_left_out_is_caught() -> None:
    problem = a_problem([a_pass(1, at_minute=0), a_pass(2, at_minute=60)])
    outcome = greedy(problem)

    dropped = ScheduleOutcome(selected=outcome.selected[:1], rejected=[])

    assert rules_broken(problem, dropped) == {"undecided"}


def test_a_candidate_decided_twice_is_caught() -> None:
    problem = a_problem([a_pass(1, at_minute=0), a_pass(2, at_minute=5)])
    outcome = greedy(problem)
    twice = ScheduleOutcome(
        selected=outcome.selected,
        rejected=[*outcome.rejected, outcome.rejected[0]],
    )

    assert rules_broken(problem, twice) == {"decided_twice"}


def test_a_pass_nobody_asked_about_is_caught() -> None:
    problem = a_problem([a_pass(1, at_minute=0)])
    stray = ScheduleOutcome(
        selected=ranked(a_pass(1, at_minute=0), a_pass(2, at_minute=60)), rejected=[]
    )

    assert rules_broken(problem, stray) == {"not_a_candidate"}


def test_a_commitment_selected_again_is_caught() -> None:
    fixed = a_pass(1, at_minute=0)
    problem = a_problem([], commitments=(Commitment(fixed, "as_1"),))
    again = ScheduleOutcome(selected=ranked(fixed), rejected=[])

    assert "recommitted" in rules_broken(problem, again)

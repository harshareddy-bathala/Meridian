"""The optimiser: best under the constraints, always valid, and repeatable (D-167).

Optimality is checked against brute force on instances small enough to
enumerate, not assumed from the solver's status. Every outcome is also put
through the validator that guards the scheduler run, so a schedule the solver
got wrong cannot pass here by agreeing with itself.
"""

from __future__ import annotations

import itertools
import math
import random
from datetime import UTC, datetime, timedelta

import highspy
import pytest

import meridian.scheduler.optimiser as optimiser_module
from meridian.scheduler import (
    Candidate,
    Commitment,
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
from meridian.scheduler.optimiser import SolverSettings, optimise
from meridian.scheduler.programme import Answer, options, read_answer

T0 = datetime(2026, 9, 28, tzinfo=UTC)
STATIONS = ("st_001", "st_002")
FIXED = Rules(turnaround_s=0.0)
QUICK = SolverSettings(time_limit_s=10.0)


def a_pass(
    pass_id: int,
    *,
    at_minute: float,
    minutes: float = 11.0,
    station_id: str = STATIONS[0],
    margin_s: float = 0.0,
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


def scored(candidate: Candidate, score: float) -> ScoredCandidate:
    return ScoredCandidate(candidate=candidate, score=score)


def greedy(
    candidates: list[ScoredCandidate],
    rules: Rules = FIXED,
    committed: tuple[Commitment, ...] = (),
) -> list[int]:
    ranked = sorted(
        candidates,
        key=lambda one: (-one.score, one.candidate.aos, one.candidate.pass_id),
    )
    outcome = select_without_conflict(ranked, rules=rules, committed=committed)
    return sorted(one.candidate.pass_id for one in outcome.selected)


def total(candidates: list[ScoredCandidate], chosen: list[int]) -> float:
    by_id = {one.candidate.pass_id: one.score for one in candidates}
    return math.fsum(by_id[pass_id] for pass_id in chosen)


# --- the case greedy loses ------------------------------------------------------


def test_two_decent_passes_beat_the_one_high_pass_between_them() -> None:
    """The textbook case: 70 overlaps both 40s, which do not overlap each other.

    Greedy takes 70 and loses both; the optimum takes the two for 80.
    """
    candidates = [
        scored(a_pass(1, at_minute=0), 40.0),
        scored(a_pass(2, at_minute=8), 70.0),
        scored(a_pass(3, at_minute=16), 40.0),
    ]

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert greedy(candidates) == [2]
    assert sorted(one.candidate.pass_id for one in result.outcome.selected) == [1, 3]
    assert result.run.status == "optimal"
    assert result.run.objective == 80.0
    assert [
        (one.rule, one.conflicts_with_pass_id) for one in result.outcome.rejected
    ] == [("overlap", 1)]


def test_the_run_says_what_solved_it() -> None:
    result = optimise(
        [scored(a_pass(1, at_minute=0), 10.0)], rules=FIXED, settings=QUICK
    )

    assert result.run.solver == "highs"
    assert result.run.version
    assert result.run.bound == pytest.approx(result.run.objective)
    assert result.run.time_limit_s == QUICK.time_limit_s
    assert result.run.detail is None


def test_no_candidates_is_an_empty_optimal_schedule() -> None:
    result = optimise([], rules=FIXED, settings=QUICK)

    assert (result.outcome.selected, result.outcome.rejected) == ([], [])
    assert (result.run.status, result.run.objective) == ("optimal", 0.0)


# --- optimal, against brute force -------------------------------------------------


def random_instance(
    seed: int, most: int, span_minutes: float = 240.0
) -> tuple[list[ScoredCandidate], tuple[Commitment, ...], Rules]:
    """Up to ``most`` passes over two stations and ``span_minutes``, seeded."""
    rng = random.Random(seed)
    candidates = [
        scored(
            a_pass(
                pass_id,
                at_minute=rng.uniform(0, span_minutes),
                minutes=rng.uniform(4, 15),
                margin_s=rng.choice([0.0, 0.5, 30.0]),
                station_id=rng.choice(STATIONS),
            ),
            round(rng.uniform(1, 90), 3),
        )
        for pass_id in range(1, rng.randint(1, most) + 1)
    ]
    commitments = tuple(
        Commitment(
            a_pass(
                900 + number,
                at_minute=rng.uniform(-30, span_minutes + 20),
                station_id=rng.choice(STATIONS),
            ),
            f"as_c{number}",
        )
        for number in range(rng.randint(0, 2))
    )
    rules = Rules(
        turnaround_s=rng.choice([0.0, 90.0]), most_eligible=rng.choice([2, 3, 8])
    )
    return candidates, commitments, rules


def brute_force_best(
    candidates: list[ScoredCandidate], commitments: tuple[Commitment, ...], rules: Rules
) -> float:
    """The largest total of any subset every rule allows."""
    fixed = [one.candidate for one in commitments]
    best = 0.0
    for size in range(len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            chosen = [one.candidate for one in subset]
            held = [*chosen, *fixed]
            if any(
                overlaps(one, other, rules.turnaround_s)
                for index, one in enumerate(chosen)
                for other in [*chosen[index + 1 :], *fixed]
            ):
                continue
            if any(
                exceeds_cap(one, [other for other in held if other is not one], rules)
                for one in chosen
            ):
                continue
            best = max(best, math.fsum(one.score for one in subset))
    return best


@pytest.mark.parametrize("seed", range(60))
def test_optimal_against_brute_force(seed: int) -> None:
    candidates, commitments, rules = random_instance(seed, most=11)

    result = optimise(candidates, rules=rules, settings=QUICK, committed=commitments)

    assert result.run.status == "optimal"
    assert result.run.objective == pytest.approx(
        brute_force_best(candidates, commitments, rules)
    )


def test_the_brute_force_instances_are_not_trivial() -> None:
    """Positive control: on some of them greedy is beaten, and the cap binds.

    Greedy is close to optimal on random intervals with random scores, and is
    beaten on 4 of these 60; the textbook case above is the one that matters.
    Were the solver doing no better than greedy, this would fail.
    """
    beaten = capped = 0
    for seed in range(60):
        candidates, commitments, rules = random_instance(seed, most=11)
        result = optimise(
            candidates, rules=rules, settings=QUICK, committed=commitments
        )
        greedy_total = total(candidates, greedy(candidates, rules, commitments))
        beaten += result.run.objective > greedy_total + 1e-9
        capped += any(one.rule == "eligible_cap" for one in result.outcome.rejected)

    assert beaten >= 3
    assert capped >= 1


@pytest.mark.parametrize("seed", range(200))
def test_valid_and_never_worse_than_greedy(seed: int) -> None:
    candidates, commitments, rules = random_instance(1000 + seed, most=30)

    result = optimise(candidates, rules=rules, settings=QUICK, committed=commitments)
    problem = Problem(
        candidates=tuple(one.candidate for one in candidates),
        commitments=commitments,
        unavailable=frozenset(),
        rules=rules,
    )

    assert violations(problem, result.outcome) == ()
    assert (
        result.run.objective
        >= total(candidates, greedy(candidates, rules, commitments)) - 1e-9
    )


# --- repeatable ------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
def test_the_order_candidates_arrive_in_changes_nothing(seed: int) -> None:
    candidates, commitments, rules = random_instance(2000 + seed, most=30)
    shuffled = list(candidates)
    random.Random(seed).shuffle(shuffled)

    first = optimise(candidates, rules=rules, settings=QUICK, committed=commitments)
    second = optimise(shuffled, rules=rules, settings=QUICK, committed=commitments)

    assert first.outcome == second.outcome
    assert first.run.objective == second.run.objective


# --- commitments and reasons -------------------------------------------------------


def test_a_commitment_blocks_before_the_solver_is_asked() -> None:
    commitment = Commitment(a_pass(9, at_minute=0), "as_earlier")
    candidates = [
        scored(a_pass(1, at_minute=5), 90.0),
        scored(a_pass(2, at_minute=30), 10.0),
    ]

    result = optimise(candidates, rules=FIXED, settings=QUICK, committed=(commitment,))

    assert [one.candidate.pass_id for one in result.outcome.selected] == [2]
    (rejection,) = result.outcome.rejected
    assert (rejection.rule, rejection.committed_assignment_id) == (
        "overlap",
        "as_earlier",
    )


def test_an_overlap_names_the_best_selection_it_lost_to() -> None:
    """Pass 2 overlaps both selections; the higher-scoring one is named."""
    candidates = [
        scored(a_pass(1, at_minute=0), 30.0),
        scored(a_pass(2, at_minute=8), 20.0),
        scored(a_pass(3, at_minute=12), 50.0),
    ]

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert sorted(one.candidate.pass_id for one in result.outcome.selected) == [1, 3]
    assert [one.conflicts_with_pass_id for one in result.outcome.rejected] == [3]


# --- refusals ---------------------------------------------------------------------


@pytest.mark.parametrize("score", [-1.0, math.nan, math.inf])
def test_a_score_that_is_not_a_value_is_refused(score: float) -> None:
    with pytest.raises(ValueError, match="finite, non-negative"):
        optimise([scored(a_pass(1, at_minute=0), score)], rules=FIXED, settings=QUICK)


@pytest.mark.parametrize("limit", [0.0, -1.0, math.inf, math.nan])
def test_a_time_limit_that_cannot_work_is_refused(limit: float) -> None:
    with pytest.raises(ValueError, match="time_limit_s"):
        SolverSettings(time_limit_s=limit)


# --- when the solver gives nothing usable ------------------------------------------


def a_crowded_instance() -> list[ScoredCandidate]:
    return [
        scored(a_pass(number, at_minute=number * 5), 10.0 + number)
        for number in range(6)
    ]


def test_no_answer_falls_back_to_greedy_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        optimiser_module,
        "solve",
        lambda *_: Answer(
            None, "fallback", None, 0.1, "the solver stopped with Time limit reached"
        ),
    )
    candidates = a_crowded_instance()

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert result.run.status == "fallback"
    assert result.run.bound is None
    assert "Time limit" in (result.run.detail or "")
    assert sorted(one.candidate.pass_id for one in result.outcome.selected) == greedy(
        candidates
    )


def test_an_answer_that_breaks_a_rule_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The solver's schedule is a claim, and this one claims every pass."""
    candidates = a_crowded_instance()
    monkeypatch.setattr(
        optimiser_module,
        "solve",
        lambda *_: Answer(frozenset(range(6)), "optimal", 999.0, 0.1, None),
    )

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert result.run.status == "fallback"
    assert result.run.detail == "the solver's schedule broke overlap"
    assert sorted(one.candidate.pass_id for one in result.outcome.selected) == greedy(
        candidates
    )


def test_a_partial_answer_at_the_time_limit_is_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped early with nothing chosen: whatever still fits is taken, so
    every rejection has a reason and the schedule is valid."""
    candidates = [
        scored(a_pass(1, at_minute=0), 10.0),
        scored(a_pass(2, at_minute=60), 20.0),
    ]
    monkeypatch.setattr(
        optimiser_module,
        "solve",
        lambda *_: Answer(frozenset(), "time_limit", 30.0, 10.0, None),
    )

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert result.run.status == "time_limit"
    assert sorted(one.candidate.pass_id for one in result.outcome.selected) == [1, 2]


def test_turnaround_keeps_two_passes_a_minute_apart_from_both_being_taken() -> None:
    """Sixty seconds between them, ninety needed: the solver itself must see
    the turnaround, not leave it to the check and fall back."""
    candidates = [
        scored(a_pass(1, at_minute=0), 40.0),
        scored(a_pass(2, at_minute=12), 30.0),
    ]

    result = optimise(candidates, rules=Rules(turnaround_s=90.0), settings=QUICK)

    assert result.run.status == "optimal"
    assert [one.candidate.pass_id for one in result.outcome.selected] == [1]


def test_the_solver_is_asked_for_a_proven_best_on_one_seeded_thread() -> None:
    """What makes the answer repeatable and ``optimal`` mean proven (D-167)."""
    chosen = dict(options(SolverSettings(time_limit_s=7.0, seed=3)))

    assert chosen["mip_rel_gap"] == 0.0
    assert chosen["threads"] == 1
    assert chosen["random_seed"] == 3
    assert chosen["time_limit"] == 7.0


class _Stopped:
    """A solver that ran out of time, with or without something to show."""

    def __init__(self, solution_status: int) -> None:
        self._status = solution_status

    def getModelStatus(self) -> highspy.HighsModelStatus:  # noqa: N802
        return highspy.HighsModelStatus.kTimeLimit

    def getInfo(self) -> object:  # noqa: N802
        return type(
            "Info", (), {"primal_solution_status": self._status, "mip_dual_bound": 9.0}
        )()

    def getRunTime(self) -> float:  # noqa: N802
        return 10.0

    def modelStatusToString(self, _status: object) -> str:  # noqa: N802
        return "Time limit reached"

    def getSolution(self) -> object:  # noqa: N802
        return type("Solution", (), {"col_value": [1.0, 0.0]})()


def test_a_time_limit_with_nothing_feasible_is_not_an_answer() -> None:
    stopped = read_answer(_Stopped(solution_status=0))  # type: ignore[arg-type]
    found = read_answer(_Stopped(solution_status=2))  # type: ignore[arg-type]

    assert (stopped.chosen, stopped.status) == (None, "fallback")
    assert (found.chosen, found.status) == (frozenset({0}), "time_limit")


def test_a_pass_added_after_the_limit_can_be_the_one_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped holding only pass 1; pass 3 still fits and is taken. Pass 2
    overlaps both, and is named against 3, the better of the two."""
    candidates = [
        scored(a_pass(1, at_minute=0), 10.0),
        scored(a_pass(2, at_minute=8), 5.0),
        scored(a_pass(3, at_minute=16), 50.0),
    ]
    monkeypatch.setattr(
        optimiser_module,
        "solve",
        lambda *_: Answer(frozenset({0}), "time_limit", 60.0, 10.0, None),
    )

    result = optimise(candidates, rules=FIXED, settings=QUICK)

    assert [one.candidate.pass_id for one in result.outcome.selected] == [3, 1]
    assert [one.conflicts_with_pass_id for one in result.outcome.rejected] == [3]

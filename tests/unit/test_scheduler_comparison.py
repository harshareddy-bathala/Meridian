"""The comparison's arithmetic, its configuration, and the oracle's values — D-172.

The paired bootstrap is checked on days written by hand: its point estimate is
the plain difference, it is the same for one seed and differs for another,
and its interval holds the estimate. The configuration is refused where it is
wrong and hashed without its model paths. The oracle values a pass by what it
decoded, and an unknown outcome at nothing.

Reference: docs/DECISIONS.md D-151, D-160, D-172.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.prediction import replay as prediction_replay
from meridian.scheduler import Candidate
from meridian.scheduler.comparison import paired_gain, totals
from meridian.scheduler.comparison_config import (
    comparison_config_sha256,
    parse_comparison_config,
)
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import Rules
from meridian.scheduler.elevation_baseline import rank_by_elevation
from meridian.scheduler.optimiser import SolverSettings
from meridian.scheduler.oracle import oracle_scores, schedule_oracle
from meridian.scheduler.priority_baseline import NEUTRAL_PRIORITY
from meridian.scheduler.replay import DayResult
from meridian.scheduler.schedule_config import ScheduleConfigError

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "deploy" / "schedule-evaluation.toml.example"
)
MODELS = '[models]\nA = "models/a"\nC = "models/c"\nD = "models/d"\n'
T0 = datetime(2026, 9, 16, tzinfo=UTC)


def day(frames: int, *, unknown: int = 0, status: str = "optimal") -> DayResult:
    return DayResult(selected=(1, 2), frames=frames, unknown=unknown, status=status)


# --- totals and the gain ------------------------------------------------------


def test_totals_sum_the_days_and_count_how_each_was_solved() -> None:
    summed = totals(
        [day(10, unknown=1), day(5, status="time_limit"), day(0, unknown=2)]
    )

    assert (summed.selected, summed.frames, summed.unknown) == (6, 15, 3)
    assert summed.statuses == (("optimal", 2), ("time_limit", 1))
    assert summed.unknown_share == 0.5
    assert summed.per_hour(30.0) == 0.5


def test_the_gain_is_the_difference_per_station_hour_and_over_the_second() -> None:
    first = [day(30), day(20)]
    second = [day(20), day(20)]

    gain = paired_gain(first, second, [24.0, 16.0], seed=0, resamples=200)

    assert gain.per_hour == 10 / 40
    assert gain.relative == 10 / 40
    assert gain.per_hour_interval.low <= gain.per_hour <= gain.per_hour_interval.high
    assert gain.resamples == 200


def test_the_interval_is_the_same_for_one_seed_and_moves_with_another() -> None:
    rng = random.Random(3)
    first = [day(rng.randint(0, 100)) for _ in range(30)]
    second = [day(rng.randint(0, 100)) for _ in range(30)]
    hours = [24.0] * 30

    one = paired_gain(first, second, hours, seed=7, resamples=500)
    again = paired_gain(first, second, hours, seed=7, resamples=500)
    other = paired_gain(first, second, hours, seed=8, resamples=500)

    assert one == again
    assert one.per_hour == other.per_hour
    assert one.per_hour_interval != other.per_hour_interval


def test_identical_schedulers_gain_nothing_with_certainty() -> None:
    same = [day(12), day(40), day(7)]

    gain = paired_gain(same, same, [24.0] * 3, seed=0, resamples=100)

    assert gain.per_hour == 0.0
    assert (gain.per_hour_interval.low, gain.per_hour_interval.high) == (0.0, 0.0)


def test_no_relative_gain_over_a_scheduler_that_took_no_frames() -> None:
    gain = paired_gain([day(5)], [day(0)], [24.0], seed=0, resamples=100)

    assert gain.relative is None
    assert gain.relative_interval is None


def test_a_resample_without_the_second_s_frames_leaves_no_relative_interval() -> None:
    gain = paired_gain(
        [day(5), day(5)], [day(0), day(4)], [24.0, 24.0], seed=0, resamples=200
    )

    assert gain.relative == 6 / 4
    assert gain.relative_interval is None


@pytest.mark.parametrize(
    ("first", "second", "hours"),
    [([], [], []), ([day(1)], [], [24.0]), ([day(1)], [day(1)], [])],
)
def test_unaligned_or_empty_days_are_refused(
    first: list[DayResult], second: list[DayResult], hours: list[float]
) -> None:
    with pytest.raises(ValueError, match="same station-days"):
        paired_gain(first, second, hours, seed=0, resamples=100)


# --- the configuration ----------------------------------------------------------


def test_the_example_file_is_accepted_as_it_stands() -> None:
    config = parse_comparison_config(EXAMPLE.read_text(encoding="utf-8"))

    assert sorted(config.models) == ["A", "C", "D"]
    assert (config.resamples, config.threshold) == (2000, None)
    assert config.schedule.time_limit_s == 10.0


def test_settings_are_read_into_the_schedule_s_own_checks() -> None:
    config = parse_comparison_config(
        f'frames = "none"\nseed = 4\nthreshold = 0.5\nresamples = 300\n{MODELS}'
    )

    assert (config.schedule.frames, config.schedule.seed) == ("none", 4)
    assert (config.threshold, config.resamples) == (0.5, 300)
    assert config.model_paths(Path("/root")) == {
        "A": Path("/root/models/a"),
        "C": Path("/root/models/c"),
        "D": Path("/root/models/d"),
    }


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('[models]\nA = "a"\nD = "d"\n', "names the directories of A, C, D"),
        (MODELS + 'B = "b"\n', "names the directories of A, C, D"),
        ('[models]\nA = "a"\nC = ""\nD = "d"\n', "names the directories"),
        ('models = "a"\n', "must be a table"),
        (f"resamples = 99\n{MODELS}", "resamples must be"),
        (f"resamples = true\n{MODELS}", "resamples must be"),
        (f"threshold = 0\n{MODELS}", "threshold must be"),
        (f"threshold = 1.5\n{MODELS}", "threshold must be"),
        (f"time_limit_s = 0\n{MODELS}", "time_limit_s must be"),
        (f'configuration = "D"\n{MODELS}', "unknown comparison settings"),
        ("models = [", "is not TOML"),
    ],
)
def test_a_configuration_that_cannot_be_obeyed_is_refused(
    text: str, match: str
) -> None:
    with pytest.raises(ScheduleConfigError, match=match):
        parse_comparison_config(text)


def test_the_hash_is_of_the_values_and_not_where_the_models_are_kept() -> None:
    here = parse_comparison_config(MODELS)
    there = parse_comparison_config(MODELS.replace("models/", "/elsewhere/"))
    seeded = parse_comparison_config(f"seed = 1\n{MODELS}")

    assert comparison_config_sha256(here) == comparison_config_sha256(there)
    assert comparison_config_sha256(here) != comparison_config_sha256(seeded)


# --- the oracle -----------------------------------------------------------------


def candidate(pass_id: int, minute: float, elevation: float = 40.0) -> Candidate:
    aos = T0 + timedelta(minutes=minute)
    return Candidate(
        pass_id=pass_id,
        station_id="st_a",
        aos=aos,
        los=aos + timedelta(minutes=10),
        margin_s=0.0,
        max_elevation_deg=elevation,
        priority=1.0,
        simulated=False,
    )


def test_the_oracle_values_a_pass_by_its_frames_and_an_unknown_one_at_nothing() -> None:
    scored = oracle_scores(
        [candidate(1, 0), candidate(2, 20), candidate(3, 40)], {1: 120, 2: None}
    )

    assert [one.score for one in scored] == [120.0, 0.0, 0.0]


def test_the_oracle_takes_what_decoded_where_elevation_would_not() -> None:
    """Pass 1 is higher and decoded less; 2 and 3 overlap it and decoded more."""
    high = candidate(1, 5, elevation=80.0)
    lower = [candidate(2, 0, elevation=30.0), candidate(3, 10, elevation=30.0)]
    frames = {1: 100, 2: 90, 3: 80}
    rules = Rules(turnaround_s=0.0)

    found = schedule_oracle(
        [high, *lower], frames, rules=rules, settings=SolverSettings(time_limit_s=5.0)
    )
    greedy = select_without_conflict(rank_by_elevation([high, *lower]), rules=rules)

    assert found.run.status == "optimal"
    assert sorted(one.candidate.pass_id for one in found.outcome.selected) == [2, 3]
    assert [one.candidate.pass_id for one in greedy.selected] == [1]


def test_the_replay_and_the_live_run_give_an_unknown_satellite_one_priority() -> None:
    """Two constants, since prediction may not import the scheduler; if they
    drifted, B and D would be replayed under priorities no live run used."""
    assert prediction_replay.NEUTRAL_PRIORITY == NEUTRAL_PRIORITY

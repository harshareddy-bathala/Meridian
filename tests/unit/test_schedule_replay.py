"""The retrospective comparison, through the commands: replay, oracle, report.

The world is 22 days over two stations, and every pass is one of an
overlapping pair: one satellite that rarely decodes and one that often does,
rising six minutes apart, so one antenna takes one of the two. The historical
policy took the higher of each pair, as greedy A does, and the other was never
attempted, so its outcome is unknown. Half of each day was attempted, and the
comparison is read at a threshold of 0.4 to keep those days. The last day's
windows close within a day of the snapshot, so only its first pair per station
has settled (D-146).

A, C and D are fitted by ``meridian model fit`` and compared by ``meridian
schedule evaluate``, so every candidate, prediction and outcome is the
pipeline's own.

Reference: docs/DECISIONS.md D-151, D-160, D-166, D-172.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.prediction.replay import Outcome, Replay, ReplayPass, load_replay
from meridian.prediction.score import sigmoid
from meridian.scheduler import ScheduleOutcome
from meridian.scheduler import replay as replay_module
from meridian.scheduler.comparison import paired_gain
from meridian.scheduler.replay import (
    ORACLE,
    SCHEDULERS,
    ReplayInvalidError,
    ReplayResults,
    candidate_of,
    replay_schedules,
)
from meridian.scheduler.schedule_config import ScheduleConfig

SINCE = datetime(2026, 9, 1, tzinfo=UTC)
DAYS = 22
ONE_DAY = timedelta(days=1)
RARELY = "norad:57166"
OFTEN = "norad:59051"
STATIONS = ("st_a", "st_b")

SETTINGS = (
    "min_station_history = 5\n"
    "folds = 0\n"
    "train_until = 2026-09-11T00:00:00Z\n"
    "validate_until = 2026-09-16T00:00:00Z\n"
)

FRESH = (
    sys.executable,
    "-c",
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))",
)

Rows = dict[str, list[Mapping[str, object]]]


def replay_world(
    priorities: Mapping[str, float] | None = None, *, counted: bool = True
) -> Rows:
    """Overlapping pairs; the higher of each taken and reported, the other not.

    ``counted`` false: the reports say what they decoded, and not how much.
    """
    rng = random.Random(18)
    rows: Rows = {"passes": [], "assignments": [], "observations": []}
    pass_id = 0
    for day in range(DAYS):
        for slot in range(4):
            for offset, station in enumerate(STATIONS):
                start = SINCE + timedelta(days=day, hours=6 * slot + offset + 1)
                pair = []
                for index, (satellite, odds) in enumerate(
                    ((RARELY, 0.3), (OFTEN, 1.0))
                ):
                    pass_id += 1
                    aos = start + timedelta(minutes=6 * index)
                    elevation = rng.uniform(5.0, 85.0)
                    decoded = rng.random() < odds * sigmoid((elevation - 30.0) / 10.0)
                    frames = rng.randint(20, 400) if decoded else 0
                    pair.append(
                        (
                            {
                                "id": pass_id,
                                "satellite_id": satellite,
                                "station_id": station,
                                "aos": aos,
                                "los": aos + timedelta(minutes=12),
                                "max_elevation_deg": elevation,
                                "aos_azimuth_deg": rng.uniform(0.0, 360.0),
                                "los_azimuth_deg": rng.uniform(0.0, 360.0),
                                "element_set_id": 2 * day + index,
                                "computed_at": aos - timedelta(hours=5),
                                "simulated": False,
                            },
                            frames,
                        )
                    )
                taken = max(pair, key=lambda one: one[0]["max_elevation_deg"])
                for predicted, _ in pair:
                    rows["passes"].append(predicted)
                reported = taken[1] if counted or not taken[1] else None
                _report(rows, taken[0], reported)
    satellites = (RARELY, OFTEN)
    return rows | {
        "element_sets": [
            {
                "id": 2 * day + index,
                "satellite_id": satellite,
                "epoch": SINCE + timedelta(days=day),
            }
            for day in range(DAYS)
            for index, satellite in enumerate(satellites)
        ],
        "stations": [{"station_id": station, "lon_deg": 77.6} for station in STATIONS],
        "satellites": [
            {"satellite_id": one, "priority": (priorities or {}).get(one, 1.0)}
            for one in satellites
        ],
        "transmitters": [
            {
                "id": index + 1,
                "satellite_id": one,
                "centre_freq_hz": 137_900_000,
                "active": True,
                "deleted_at": None,
            }
            for index, one in enumerate(satellites)
        ],
    }


def _report(rows: Rows, predicted: Mapping[str, object], frames: int | None) -> None:
    """A report; ``None`` frames is a decode whose client gave no count."""
    number = predicted["id"]
    decoded = frames is None or frames > 0
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": predicted["station_id"],
            "start_at": predicted["aos"],
            "end_at": predicted["los"],
            "decision": "scheduled",
            "state": "reported",
            "model_config": "A",
            "simulated": False,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded" if decoded else "signal_no_decode",
            "first_detection_at": None,
            "noise_floor_dbfs": None,
            "simulated": False,
        }
        | ({} if frames is None else {"frames_decoded": frames})
    )


def printed_path(out: str) -> Path:
    first = out.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


class Comparison:
    """A dataset, its three models, and the comparison's configuration file."""

    def __init__(
        self, root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.root = root
        self.capsys = capsys
        self.raw_snapshot = raw_snapshot
        self.dataset = Path()
        self.models: dict[str, Path] = {}

    def run(self, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = main([*args[:1], "--root", str(self.root), *args[1:]])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err

    def ok(self, *args: str) -> str:
        code, out, err = self.run(*args)
        assert code == 0, err
        return out

    def build(
        self, priorities: Mapping[str, float] | None = None, *, counted: bool = True
    ) -> Comparison:
        raw = self.raw_snapshot(replay_world(priorities, counted=counted))
        self.dataset = printed_path(self.ok("snapshot", "label", str(raw)))
        for name in ("A", "C", "D"):
            self.models[name] = self.fit(name, SETTINGS)
        return self

    def fit(self, name: str, settings: str) -> Path:
        config = self.root.parent / f"model-{name}-{len(settings)}.toml"
        config.write_text(f'configuration = "{name}"\n{settings}', encoding="utf-8")
        return printed_path(
            self.ok("model", "fit", str(self.dataset), "--config", str(config))
        )

    def config(
        self, models: Mapping[str, Path] | None = None, threshold: str = "0.4"
    ) -> Path:
        chosen = models or self.models
        path = self.root.parent / "schedule-evaluation.toml"
        lines = ["resamples = 200"]
        lines += [f"threshold = {threshold}"] if threshold else []
        lines += ["[models]"]
        lines += [f'{name} = "{where}"' for name, where in sorted(chosen.items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def evaluate(
        self, models: Mapping[str, Path] | None = None, threshold: str = "0.4"
    ) -> tuple[int, str, str]:
        return self.run(
            "schedule",
            "evaluate",
            str(self.dataset),
            "--config",
            str(self.config(models, threshold)),
        )

    def replay(self) -> Replay:
        return load_replay(self.dataset, self.models, root=self.root, threshold=0.4)


@pytest.fixture
def comparison(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> Comparison:
    return Comparison(datasets_root, raw_snapshot, capsys).build()


def solved(replay: Replay) -> ReplayResults:
    return replay_schedules(replay, ScheduleConfig(time_limit_s=10.0))


def selections(results: ReplayResults, name: str) -> list[tuple[int, ...]]:
    return [one.selected for one in results.results[name]]


# --- what is replayed ---------------------------------------------------------


def test_the_test_span_s_retained_days_are_replayed_with_every_pass(
    comparison: Comparison,
) -> None:
    replay = comparison.replay()

    assert replay.test_from == datetime(2026, 9, 16, tzinfo=UTC)
    assert replay.days
    assert all(one.day >= replay.test_from.date() for one in replay.days)
    assert all(len(one.pass_ids) % 2 == 0 for one in replay.days)
    assert {one.completeness for one in replay.days} == {0.5}
    assert replay.left_out["simulated"] == 0


def test_a_window_not_yet_settled_is_no_candidate(comparison: Comparison) -> None:
    """The last day keeps its first pair per station; the three after it are
    open, and neither a candidate nor a reason to drop the day."""
    replay = comparison.replay()
    last = [one for one in replay.days if one.day == replay.as_of.date() - ONE_DAY]

    assert [len(one.pass_ids) for one in last] == [2, 2]
    assert all(
        replay.passes[pass_id].aos < replay.as_of - ONE_DAY
        for one in last
        for pass_id in one.pass_ids
    )


def test_a_decode_with_no_count_is_unknown_not_nothing(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    built = Comparison(datasets_root, raw_snapshot, capsys).build(counted=False)
    replay = built.replay()
    outcomes = list(replay.outcomes.values())

    assert {one.frames for one in outcomes if one.why == "frames_not_reported"} == {
        None
    }
    assert any(one.why == "frames_not_reported" for one in outcomes)
    assert {one.frames for one in outcomes if one.why == "signal_no_decode"} == {0}


def test_the_dataset_s_own_threshold_keeps_no_half_attempted_day(
    comparison: Comparison,
) -> None:
    """Positive control: 0.4 is what keeps the days, not a default."""
    replay = load_replay(comparison.dataset, comparison.models, root=comparison.root)

    assert replay.days == ()
    assert replay.left_out["below_threshold"] > 0


def test_a_candidate_s_window_widens_with_its_element_set_s_age() -> None:
    """The margin a live run gives it (D-060): an older set, a wider window."""
    aos = datetime(2026, 9, 20, 6, tzinfo=UTC)
    fresh, stale = (
        candidate_of(
            ReplayPass(
                pass_id=1,
                station_id="st_a",
                satellite_id=RARELY,
                aos=aos,
                los=aos + timedelta(minutes=12),
                max_elevation_deg=40.0,
                element_set_epoch=aos - age,
                priority=1.0,
            )
        )
        for age in (timedelta(hours=1), timedelta(days=5))
    )

    assert 0 < fresh.margin_s < stale.margin_s
    assert (fresh.aos, fresh.los) == (aos, aos + timedelta(minutes=12))


# --- the oracle ---------------------------------------------------------------


def test_the_oracle_takes_at_least_every_scheduler_s_frames_on_every_day(
    comparison: Comparison,
) -> None:
    results = solved(comparison.replay())
    oracle = results.results[ORACLE]

    assert {one.status for one in oracle} == {"optimal"}
    for name in SCHEDULERS:
        for mine, bound in zip(results.results[name], oracle, strict=True):
            assert mine.frames <= bound.frames, name
    assert any(
        mine.frames < bound.frames
        for mine, bound in zip(results.results["D"], oracle, strict=True)
    )


def test_greedy_a_replays_the_historical_policy_and_knows_every_outcome(
    comparison: Comparison,
) -> None:
    """The world's policy took the higher of each pair, as greedy A does."""
    results = solved(comparison.replay())

    assert sum(one.unknown for one in results.results["greedy A"]) == 0
    assert sum(one.unknown for one in results.results["D"]) > 0


def test_an_unknown_outcome_adds_no_frames_and_is_counted(
    comparison: Comparison,
) -> None:
    replay = comparison.replay()
    results = solved(replay)

    unknown_taken = 0
    for name in SCHEDULERS:
        for day in results.results[name]:
            known = [replay.outcomes[one].frames for one in day.selected]
            assert day.frames == sum(one for one in known if one is not None)
            assert day.unknown == known.count(None)
            unknown_taken += day.unknown
    assert unknown_taken > 0


# --- what each scheduler may read ----------------------------------------------


def reversed_outcomes(replay: Replay) -> Replay:
    """Every known outcome forgotten, and every unknown one a large decode."""
    return replace(
        replay,
        outcomes={
            pass_id: Outcome(frames=None if one.frames is not None else 5000, why="x")
            for pass_id, one in replay.outcomes.items()
        },
    )


def test_no_scheduler_but_the_oracle_reads_an_outcome(comparison: Comparison) -> None:
    replay = comparison.replay()
    original = solved(replay)
    reversed_ = solved(reversed_outcomes(replay))

    for name in SCHEDULERS:
        if name != ORACLE:
            assert selections(original, name) == selections(reversed_, name), name
    assert selections(original, ORACLE) != selections(reversed_, ORACLE)


def test_a_broken_schedule_stops_the_comparison(
    comparison: Comparison, monkeypatch: pytest.MonkeyPatch
) -> None:
    def take_everything(ranked: Sequence[Any], **_: object) -> ScheduleOutcome:
        return ScheduleOutcome(selected=list(ranked), rejected=[])

    monkeypatch.setattr(replay_module, "select_without_conflict", take_everything)

    with pytest.raises(ReplayInvalidError, match="greedy A broke overlap"):
        solved(comparison.replay())


# --- B is A weighted by priority ----------------------------------------------


def test_b_is_a_when_every_priority_is_neutral(comparison: Comparison) -> None:
    results = solved(comparison.replay())

    assert selections(results, "B") == selections(results, "A")
    assert selections(results, "greedy B") == selections(results, "greedy A")


def test_b_is_not_a_when_a_satellite_is_preferred(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Positive control: priority reaches B's schedule and not A's."""
    built = Comparison(datasets_root, raw_snapshot, capsys).build({RARELY: 4.0})
    results = solved(built.replay())

    assert selections(results, "B") != selections(results, "A")
    assert selections(results, "greedy B") != selections(results, "greedy A")


# --- the report ---------------------------------------------------------------


def test_the_report_states_what_it_compared_and_sc_1(comparison: Comparison) -> None:
    code, out, err = comparison.evaluate()

    assert code == 0, err
    assert out.startswith("schedule comparison over dataset ")
    assert "  model B            A's (D-160)" in out
    assert "threshold 0.4:" in out
    for name in SCHEDULERS:
        assert f"\n  {name:<10} " in out
    assert "  SC-1, D − B " in out
    assert "  D − greedy B " in out
    assert "200 bootstrap resamples" in out


def test_sc_1_is_d_against_the_optimised_b(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """With a satellite preferred, B is not A, so the line can tell them apart."""
    built = Comparison(datasets_root, raw_snapshot, capsys).build({RARELY: 4.0})
    results = solved(built.replay())
    hours = [one.hours for one in results.replay.days]
    against = {
        name: paired_gain(
            results.results["D"], results.results[name], hours, seed=0, resamples=200
        ).per_hour
        for name in ("A", "B")
    }

    code, out, err = built.evaluate()

    assert code == 0, err
    (line,) = [one for one in out.splitlines() if "SC-1" in one]
    assert against["A"] != against["B"]
    assert f" {against['B']:+.3f} frames per station-hour" in line


def test_the_report_is_the_same_twice_and_in_other_processes(
    comparison: Comparison,
) -> None:
    first = comparison.evaluate()
    again = comparison.evaluate()
    config = str(comparison.config())
    printed = []
    for seed in ("1", "2"):
        ran = subprocess.run(
            [
                *FRESH,
                "schedule",
                "--root",
                str(comparison.root),
                "evaluate",
                str(comparison.dataset),
                "--config",
                config,
            ],
            capture_output=True,
            text=True,
            check=True,
            env=os.environ | {"PYTHONHASHSEED": seed},
        )
        printed.append(ran.stdout)

    assert first == again
    assert printed == [first[1], first[1]]


# --- refusals -----------------------------------------------------------------


def test_models_of_other_split_dates_are_refused(comparison: Comparison) -> None:
    later = SETTINGS.replace("2026-09-16", "2026-09-17")
    models = comparison.models | {"C": comparison.fit("C", later)}

    code, _, err = comparison.evaluate(models)

    assert code == 1
    assert "differ in population or split dates" in err


def test_a_model_of_another_dataset_is_refused(
    comparison: Comparison,
    datasets_root: Path,
    raw_snapshot: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = Comparison(datasets_root, raw_snapshot, capsys).build({RARELY: 2.0})
    models = comparison.models | {"C": other.models["C"]}

    code, _, err = comparison.evaluate(models)

    assert code == 1
    assert "was not fitted on the dataset being replayed" in err


def test_a_model_named_as_another_configuration_is_refused(
    comparison: Comparison,
) -> None:
    models = comparison.models | {"C": comparison.models["D"]}

    code, _, err = comparison.evaluate(models)

    assert code == 1
    assert "is configuration D, named as C's model" in err


def test_a_comparison_with_no_day_to_replay_says_so(comparison: Comparison) -> None:
    code, out, err = comparison.evaluate(threshold="")

    assert code == 0, err
    assert "0 station-days replayed" in out
    assert "nothing is compared" in out
    assert "SC-1" not in out


def test_a_damaged_dataset_exits_3(comparison: Comparison) -> None:
    copied = comparison.root / "copied"
    shutil.copytree(comparison.dataset, copied)
    labels = copied / "labels.jsonl"
    labels.chmod(0o600)
    labels.write_bytes(labels.read_bytes() + b"\n")
    comparison.dataset = copied

    code, _, err = comparison.evaluate()

    assert code == 3
    assert err.startswith("meridian schedule: ")

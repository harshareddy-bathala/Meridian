"""The scheduling section: seven schedulers replayed on the test span, and SC-1.

``EVALUATION.md`` §3 measures SC-1 on schedules, not probabilities: every
retained station-day of the test span is one problem, and greedy A and B, the
optimiser under A to D, and the oracle each solve it (D-172). The section runs
exactly that — :func:`~meridian.prediction.replay.load_replay` and
:func:`~meridian.scheduler.replay.replay_schedules`, the functions ``meridian
schedule evaluate`` runs — on the A, C and D models the prediction section
fitted, and states what the roadmap asks for:

* **decoded frames, station-hours, frames per station-hour and passes taken**
  for every scheduler, with the share of passes whose outcome nobody knows;
* **constraint violations** — every schedule is checked, and a violation stops
  the run rather than being counted, so a published report has none by
  construction and says how many it checked;
* **D − B**, SC-1, and **D − greedy B**, each a paired bootstrap over
  station-days, per station-hour and relative to the second (§3);
* **oracle regret** — the oracle's frames per station-hour minus each
  scheduler's, with its interval, and each scheduler's share of the oracle.

**Runtime is measured, not computed**, so it is kept out of every hashed file
and recorded in the manifest's environment beside the other facts about the
machine (D-235). So is HiGHS's version. A day the solver's time limit cut short
is counted in the statuses, which are hashed, since only such a day can make a
figure depend on the machine.

The solver draws its seed as ``solver`` and the bootstrap as
``bootstrap.scheduling``, both from the master (D-236).

Reference: docs/DECISIONS.md D-151, D-167, D-168, D-172, D-235, D-238.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import fsum
from pathlib import Path
from statistics import median

from meridian.datasets.publish import SnapshotDirectory
from meridian.prediction.replay import MODELLED, Replay, load_replay
from meridian.prediction.replay_models import DamagedReplayError, ReplayError
from meridian.reports.config import SchedulingConfig
from meridian.scheduler.comparison import Gain, Interval, paired_gain, totals
from meridian.scheduler.programme import solver_version
from meridian.scheduler.replay import ORACLE, SCHEDULERS, DayResult, replay_schedules

__all__ = [
    "SC1_TARGET",
    "SCHEDULING_FILE",
    "SOLVER_SEED_RANGE",
    "Scheduled",
    "scheduling_section",
]

SCHEDULING_FILE = "scheduling.jsonl"

SC1_TARGET = 0.20
"""SC-1: at least 20% more decoded frames per station-hour, D over B (§1, §3)."""

SOLVER_SEED_RANGE = 2**31
"""HiGHS takes a seed below this (``ScheduleConfig``); the derived seed is
taken modulo it, and the value used is recorded."""

_PLACES = 6
_GAINS = (("D", "B", "SC-1"), ("D", "greedy B", "against existing practice"))

Row = dict[str, object]


@dataclass(frozen=True, slots=True)
class Scheduled:
    """The section's rows, and what the replay measured about the machine."""

    rows: list[Row]
    runtimes: Mapping[str, object]
    """Solver seconds per scheduler, and HiGHS's version: never hashed."""


def scheduling_section(
    dataset: SnapshotDirectory,
    raw: SnapshotDirectory,
    models: Mapping[str, Path],
    config: SchedulingConfig,
    *,
    seeds: tuple[int, int],
) -> Scheduled:
    """Replay every scheduler, or say why nothing could be replayed.

    Args:
        dataset: The evaluation dataset, verified.
        raw: The raw snapshot it was labelled from, verified.
        models: The fitted models' directories by variant name; A, C and D
            are replayed, and any missing is the reason nothing is.
        config: The ``[scheduling]`` settings.
        seeds: The solver's seed and the bootstrap's, derived from the master.

    Raises:
        ReplayInvalidError: A schedule broke a constraint. Nothing is published.
        DamagedReplayError: A directory changed while the run read it.
    """
    solver_seed, bootstrap_seed = seeds
    missing = [name for name in MODELLED if name not in models]
    if missing:
        return _not_replayed(f"models {missing} were not fitted")
    try:
        replay = load_replay(
            dataset.path,
            {name: models[name] for name in MODELLED},
            # The build published the dataset at <root>/evaluation/<hash>.
            root=dataset.path.parent.parent,
            threshold=config.threshold,
            snapshot=raw.path,
        )
    except DamagedReplayError:
        raise
    except ReplayError as exc:
        return _not_replayed(str(exc))
    if not replay.days:
        return _not_replayed(
            "no station-day of the test span reaches the completeness threshold"
        )
    schedule = replace(config.schedule, seed=solver_seed)
    results = replay_schedules(replay, schedule).results
    rows = [
        _replayed(replay, config, solver_seed, len(results)),
        *_schedulers(results, replay),
        *_gains(results, replay, bootstrap_seed, config.resamples),
    ]
    rows.append(_sc1(rows))
    return Scheduled(rows=rows, runtimes=_runtimes(results))


def _not_replayed(reason: str) -> Scheduled:
    rows: list[Row] = [
        {"row": "replay", "status": "not replayed", "reason": reason},
        {"row": "sc1", "status": "not measured", "reason": reason},
    ]
    return Scheduled(rows=rows, runtimes={"solver": f"highs {solver_version()}"})


def _replayed(
    replay: Replay, config: SchedulingConfig, solver_seed: int, schedulers: int
) -> Row:
    """What was replayed, what was left out, and how every schedule was solved."""
    outcomes = [replay.outcomes.get(one) for one in replay.passes]
    return {
        "row": "replay",
        "status": "replayed",
        "dataset_sha256": replay.dataset_sha256,
        "models": dict(replay.models),
        "test_from": replay.test_from,
        "as_of": replay.as_of,
        "threshold": replay.threshold,
        "station_days": len(replay.days),
        "left_out": dict(replay.left_out),
        "candidates": len(replay.passes),
        "known_outcomes": sum(
            1 for one in outcomes if one is not None and one.frames is not None
        ),
        "station_hours": _real(fsum(day.hours for day in replay.days)),
        "frames_term": config.schedule.frames,
        "time_limit_s": float(config.schedule.time_limit_s),
        "turnaround_s": float(config.schedule.turnaround_s),
        "solver_seed": solver_seed,
        "schedules_checked": schedulers * len(replay.days),
        "violations": 0,
        "resamples": config.resamples,
    }


def _schedulers(
    results: Mapping[str, Sequence[DayResult]], replay: Replay
) -> list[Row]:
    hours = fsum(day.hours for day in replay.days)
    oracle = totals(results[ORACLE]).frames
    rows: list[Row] = []
    for name in SCHEDULERS:
        summed = totals(results[name])
        rows.append(
            {
                "row": "scheduler",
                "name": name,
                "selected": summed.selected,
                "frames": summed.frames,
                "unknown": summed.unknown,
                "unknown_share": _maybe(summed.unknown_share),
                "per_hour": _real(summed.per_hour(hours)),
                "of_oracle": _maybe(summed.frames / oracle if oracle else None),
                "statuses": dict(summed.statuses),
            }
        )
    return rows


def _gains(
    results: Mapping[str, Sequence[DayResult]],
    replay: Replay,
    seed: int,
    resamples: int,
) -> list[Row]:
    """D − B, D − greedy B, and the oracle's lead over every scheduler."""
    hours = [day.hours for day in replay.days]

    def gain(first: str, second: str) -> Gain:
        return paired_gain(
            results[first], results[second], hours, seed=seed, resamples=resamples
        )

    rows: list[Row] = [
        {"row": "gain", "first": first, "second": second, "label": label}
        | _gain(gain(first, second))
        for first, second, label in _GAINS
    ]
    rows.extend(
        {"row": "regret", "scheduler": name} | _gain(gain(ORACLE, name))
        for name in SCHEDULERS
        if name != ORACLE
    )
    return rows


def _gain(found: Gain) -> Row:
    return {
        "per_hour": _real(found.per_hour),
        "per_hour_interval": _interval(found.per_hour_interval),
        "relative": _maybe(found.relative),
        "relative_interval": _interval(found.relative_interval),
    }


def _sc1(rows: Sequence[Row]) -> Row:
    """SC-1 from the relative D − B gain: the point, and its whole interval."""
    found = next(one for one in rows if one["row"] == "gain" and one["second"] == "B")
    relative, interval = found["relative"], found["relative_interval"]
    row: Row = {"row": "sc1", "target": SC1_TARGET}
    if relative is None:
        return row | {"status": "not measured", "reason": "B decoded no frames"}
    return row | {
        "status": "measured",
        "relative": relative,
        "interval": interval,
        "point_meets": isinstance(relative, float) and relative >= SC1_TARGET,
        "interval_above": isinstance(interval, dict) and interval["low"] >= SC1_TARGET,
    }


def _runtimes(results: Mapping[str, Sequence[DayResult]]) -> dict[str, object]:
    """Each solver-run scheduler's seconds: days, total, median and slowest."""
    spent: dict[str, object] = {}
    for name in SCHEDULERS:
        seconds = [one.runtime_s for one in results[name] if one.runtime_s is not None]
        if seconds:
            spent[name] = {
                "days": len(seconds),
                "total": round(fsum(seconds), 3),
                "median": round(median(seconds), 4),
                "slowest": round(max(seconds), 4),
            }
    return {"solver": f"highs {solver_version()}", "schedulers": spent}


def _interval(found: Interval | None) -> dict[str, float] | None:
    if found is None:
        return None
    return {"low": _real(found.low), "high": _real(found.high)}


def _real(value: float) -> float:
    return round(value, _PLACES)


def _maybe(value: float | None) -> float | None:
    return None if value is None else _real(value)

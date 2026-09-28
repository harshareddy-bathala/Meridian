"""Every scheduler, run again over the past, on the same problem — D-172.

The roadmap asks that every scheduler compared be given the same candidates,
the same constraints, the same horizon, the same station state and the same
runtime limit. Each retained station-day of the test span is one problem, and
seven schedulers solve it:

* **greedy A** and **greedy B** — Stage 7's baselines, existing practice:
  rank by elevation, or elevation × priority, and take what fits;
* **A**, **B**, **C** and **D** — the optimiser under each configuration's
  objective (D-168), valued by each configuration's model: A's for A and B;
* **the oracle** — the optimiser valued by what each pass decoded
  (:mod:`meridian.scheduler.oracle`).

The problem is the same for all seven: the day's candidates, D-166's rules at
the configured turnaround, no commitments, and every station available, since
a retained day is one the station was attempting passes on. A schedule is
checked by :func:`~meridian.scheduler.constraints.violations` like a live one,
and a broken one stops the comparison rather than being scored.

**A pass belongs to the day it rises on**, so two passes either side of
midnight are in two problems, and each may be taken; every scheduler is
treated alike.

**Only the oracle and the tally read outcomes.** The six schedulers are handed
candidates and predictions, and a test reverses every outcome and finds their
schedules unmoved.

Reference: docs/DECISIONS.md D-065, D-160, D-166 to D-168, D-172.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from meridian.orbit.uncertainty import timing_uncertainty_at_age
from meridian.prediction.replay import Replay, ReplayPass
from meridian.prediction.score import Prediction
from meridian.scheduler import Candidate, ScheduleOutcome
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import Problem, Rules, violations
from meridian.scheduler.elevation_baseline import rank_by_elevation
from meridian.scheduler.objective import modelled, value_candidates
from meridian.scheduler.optimiser import SolverSettings, optimise
from meridian.scheduler.oracle import schedule_oracle
from meridian.scheduler.priority_baseline import rank_by_priority_weighted_elevation
from meridian.scheduler.programme import solver_version
from meridian.scheduler.schedule_config import ScheduleConfig

__all__ = [
    "GREEDY",
    "ORACLE",
    "SCHEDULERS",
    "DayResult",
    "ReplayInvalidError",
    "ReplayResults",
    "candidate_of",
    "replay_schedules",
]

GREEDY = "greedy"
"""The status of a schedule no solver made."""

ORACLE = "oracle"
SCHEDULERS = ("greedy A", "greedy B", "A", "B", "C", "D", ORACLE)
"""In the order they are reported."""

_MODEL_OF = {"A": "A", "B": "A", "C": "C", "D": "D"}
"""Which model values each configuration: B's is A's (D-160)."""


class ReplayInvalidError(RuntimeError):
    """A scheduler produced a schedule its own constraints reject."""


@dataclass(frozen=True, slots=True)
class DayResult:
    """What one scheduler took on one station-day, and what that returned."""

    selected: tuple[int, ...]
    """The passes taken, by id, sorted."""

    frames: int
    """Frames decoded by the passes taken whose outcome is known."""

    unknown: int
    """Passes taken whose outcome nobody knows: counted, never imputed."""

    status: str
    """``greedy``, or the solver's ``optimal``, ``time_limit`` or ``fallback``."""


@dataclass(frozen=True, slots=True)
class ReplayResults:
    """The replay, how it was solved, and every scheduler's day by day."""

    replay: Replay
    config: ScheduleConfig
    """The frames term, turnaround, time limit and seed every scheduler had."""

    solver_version: str
    results: Mapping[str, tuple[DayResult, ...]]
    """By scheduler, each aligned with ``replay.days``."""


@dataclass(frozen=True, slots=True)
class _Setting:
    rules: Rules
    solver: SolverSettings
    frames_term: str


def candidate_of(one: ReplayPass) -> Candidate:
    """A replayed pass as the scheduler sees one, its margin from its age (D-060)."""
    age_s = (one.aos - one.element_set_epoch).total_seconds()
    return Candidate(
        pass_id=one.pass_id,
        station_id=one.station_id,
        aos=one.aos,
        los=one.los,
        margin_s=timing_uncertainty_at_age(age_s).sigma_s,
        max_elevation_deg=one.max_elevation_deg,
        priority=one.priority,
        simulated=False,
    )


def replay_schedules(replay: Replay, config: ScheduleConfig) -> ReplayResults:
    """Run every scheduler on every retained station-day of the replay.

    Args:
        replay: The test span's candidates, predictions and outcomes.
        config: The frames term, turnaround, time limit and seed. Its
            ``configuration`` and ``model`` are not read: every configuration
            is run, each with its own model.

    Raises:
        ReplayInvalidError: A schedule broke a constraint, naming which.
    """
    setting = _Setting(
        rules=Rules(turnaround_s=config.turnaround_s),
        solver=SolverSettings(time_limit_s=config.time_limit_s, seed=config.seed),
        frames_term=config.frames,
    )
    frames = {pass_id: one.frames for pass_id, one in replay.outcomes.items()}
    results: dict[str, list[DayResult]] = {name: [] for name in SCHEDULERS}
    for day in replay.days:
        candidates = [candidate_of(replay.passes[pass_id]) for pass_id in day.pass_ids]
        problem = Problem(tuple(candidates), (), frozenset(), setting.rules)
        for name in SCHEDULERS:
            status: str
            if name == ORACLE:
                found = schedule_oracle(
                    candidates, frames, rules=setting.rules, settings=setting.solver
                )
                outcome, status = found.outcome, found.run.status
            else:
                outcome, status = _predicted(
                    name, candidates, replay.predictions, setting
                )
            broken = violations(problem, outcome)
            if broken:
                rules = ", ".join(sorted({one.rule for one in broken}))
                message = f"{name} broke {rules} on {day.station_id} {day.day}"
                raise ReplayInvalidError(message)
            results[name].append(_tally(outcome, frames, status))
    return ReplayResults(
        replay=replay,
        config=config,
        solver_version=solver_version(),
        results={name: tuple(days) for name, days in results.items()},
    )


def _predicted(
    name: str,
    candidates: Sequence[Candidate],
    predictions: Mapping[str, Mapping[int, Prediction]],
    setting: _Setting,
) -> tuple[ScheduleOutcome, str]:
    """A schedule from what was knowable before the passes: no outcome."""
    if name == "greedy A":
        ranked = rank_by_elevation(candidates)
    elif name == "greedy B":
        ranked = rank_by_priority_weighted_elevation(candidates)
    else:
        model = predictions[_MODEL_OF[name]]
        yields = {
            one.pass_id: modelled(
                model[one.pass_id].probability,
                model[one.pass_id].path,
                model[one.pass_id].reason,
            )
            for one in candidates
        }
        scored, _ = value_candidates(
            candidates,
            configuration=name,
            frames_term=setting.frames_term,
            yields=yields,
        )
        found = optimise(scored, rules=setting.rules, settings=setting.solver)
        return found.outcome, found.run.status
    return select_without_conflict(ranked, rules=setting.rules), GREEDY


def _tally(
    outcome: ScheduleOutcome, frames: Mapping[int, int | None], status: str
) -> DayResult:
    selected = sorted(one.candidate.pass_id for one in outcome.selected)
    known = [frames.get(pass_id) for pass_id in selected]
    return DayResult(
        selected=tuple(selected),
        frames=sum(one for one in known if one is not None),
        unknown=sum(1 for one in known if one is None),
        status=status,
    )

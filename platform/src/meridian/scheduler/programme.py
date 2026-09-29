"""The mixed-integer programme behind the optimiser, and HiGHS solving it.

One binary column per free candidate, its score as the objective coefficient,
maximised, with two kinds of row (D-167):

* **overlap** — a station's windows, each followed by the turnaround, are
  intervals, so every pair that overlaps shares the start of one of them. One
  row per window start, over every window of that station holding it, allows
  at most one; identical rows are kept once.
* **the delivery cap** — eligibility runs over ``[start − lead, end]``
  (D-035). The most eligible at once is reached where some eligibility begins,
  so one row per such instant allows what the station's commitments leave of
  the cap there. This is the count
  :func:`~meridian.scheduler.constraints.exceeds_cap` makes, row for row.

The model is built from candidates already in canonical order and its rows are
added sorted, and HiGHS runs on one thread with a fixed seed and a zero gap, so
one set of candidates is one model and one answer.

Reference: docs/DECISIONS.md D-035, D-166, D-167.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import highspy
import numpy as np

from meridian.scheduler import Candidate, Commitment, ScoredCandidate
from meridian.scheduler.constraints import Rules, window

__all__ = [
    "SOLVER",
    "Answer",
    "SolverSettings",
    "SolverStatus",
    "options",
    "read_answer",
    "solve",
    "solver_version",
]

SolverStatus = Literal["optimal", "time_limit", "fallback"]
"""``optimal``: proven best. ``time_limit``: the best found when time ran out,
valid but not proven. ``fallback``: greedy's, because the solver gave nothing
usable, and ``SolverRun.detail`` says why."""

SOLVER = "highs"

_FEASIBLE = 2
"""HiGHS' ``kSolutionStatusFeasible``: a solution exists, proven best or not."""

_TAKEN = 0.5
"""A binary column above this is taken; the solver's integers carry float
tolerance, so equality with 1 is not the test."""


def _highs() -> highspy.Highs:
    """A fresh solver instance.

    ``Highs.__init__`` is the one untyped call in highspy's otherwise typed
    surface, so it is made here and nowhere else.
    """
    return highspy.Highs()  # type: ignore[no-untyped-call]


def solver_version() -> str:
    """The HiGHS release that solved, recorded with every run."""
    return _highs().version()


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """How long the solver may take, and its seed."""

    time_limit_s: float
    seed: int = 0

    def __post_init__(self) -> None:
        """Refuse a limit that could never produce an answer."""
        if not self.time_limit_s > 0 or math.isinf(self.time_limit_s):
            raise ValueError(f"time_limit_s must be positive: {self.time_limit_s}")


@dataclass(frozen=True, slots=True)
class Answer:
    """What the solver returned: its choice, if it made one."""

    chosen: frozenset[int] | None
    """Indices into the canonical order; ``None`` when there is no answer."""

    status: SolverStatus
    bound: float | None
    runtime_s: float
    detail: str | None


def options(settings: SolverSettings) -> tuple[tuple[str, bool | int | float], ...]:
    """HiGHS' options for a run: quiet, one thread, seeded, and no gap.

    One thread and a fixed seed make the search repeatable; a relative gap of
    zero makes ``optimal`` mean proven best rather than within some tolerance
    of it (D-167).
    """
    return (
        ("output_flag", False),
        ("threads", 1),
        ("random_seed", settings.seed),
        ("mip_rel_gap", 0.0),
        ("time_limit", settings.time_limit_s),
    )


def _overlap_rows(free: Sequence[Candidate], rules: Rules) -> set[tuple[int, ...]]:
    """For each window's start, every window of its station holding it.

    Two overlapping windows both hold the later one's start, so these sets
    cover every overlap. Some are subsets of others and add nothing; they are
    kept rather than pruned, because pruning is work the solver does anyway.
    """
    room = timedelta(seconds=rules.turnaround_s)
    spans = [(window(one)[0], window(one)[1] + room) for one in free]
    rows = set()
    for index, (start, _) in enumerate(spans):
        station = free[index].station_id
        members = tuple(
            other
            for other, (other_start, other_end) in enumerate(spans)
            if free[other].station_id == station and other_start <= start < other_end
        )
        if len(members) > 1:
            rows.add(members)
    return rows


def _cap_rows(
    free: Sequence[Candidate], committed: Sequence[Commitment], rules: Rules
) -> set[tuple[tuple[int, ...], int]]:
    """At each instant an eligibility begins, how many more may be eligible."""
    lead = rules.delivery_lead
    eligible = [(window(one)[0] - lead, window(one)[1]) for one in free]
    fixed = [
        (
            one.candidate.station_id,
            window(one.candidate)[0] - lead,
            window(one.candidate)[1],
        )
        for one in committed
    ]
    points = [(free[i].station_id, start) for i, (start, _) in enumerate(eligible)]
    points += [(station, start) for station, start, _ in fixed]
    rows = set()
    for station, instant in points:
        members = tuple(
            i
            for i, (start, end) in enumerate(eligible)
            if free[i].station_id == station and start <= instant <= end
        )
        held = sum(
            1
            for other, start, end in fixed
            if other == station and start <= instant <= end
        )
        if members and len(members) + held > rules.most_eligible:
            rows.add((members, max(rules.most_eligible - held, 0)))
    return rows


def solve(
    free: Sequence[ScoredCandidate],
    committed: Sequence[Commitment],
    rules: Rules,
    settings: SolverSettings,
) -> Answer:
    """Hand the programme to HiGHS and read back its choice and status."""
    candidates = [one.candidate for one in free]
    count = len(free)
    solver = _highs()
    for name, value in options(settings):
        solver.setOptionValue(name, value)
    columns = np.arange(count, dtype=np.int32)
    solver.addVars(count, np.zeros(count), np.ones(count))
    solver.changeColsIntegrality(
        count, columns, np.array([highspy.HighsVarType.kInteger] * count)
    )
    solver.changeColsCost(count, columns, np.array([one.score for one in free]))
    solver.changeObjectiveSense(highspy.ObjSense.kMaximize)
    infinity = solver.getInfinity()
    rows = [(members, 1) for members in _overlap_rows(candidates, rules)]
    rows += list(_cap_rows(candidates, committed, rules))
    for members, most in sorted(rows):
        solver.addRow(
            -infinity,
            float(most),
            len(members),
            np.array(members, dtype=np.int32),
            np.ones(len(members)),
        )
    solver.run()
    return read_answer(solver)


def read_answer(solver: highspy.Highs) -> Answer:
    """The choice a finished solver holds, and what its status means for it.

    A time limit counts as an answer only when HiGHS reports a feasible
    solution; with none, its column values mean nothing, and the run falls
    back rather than calling an empty selection the best found.
    """
    status = solver.getModelStatus()
    info = solver.getInfo()
    runtime_s = solver.getRunTime()
    feasible = info.primal_solution_status == _FEASIBLE
    if status == highspy.HighsModelStatus.kOptimal:
        verdict: SolverStatus = "optimal"
    elif status == highspy.HighsModelStatus.kTimeLimit and feasible:
        verdict = "time_limit"
    else:
        detail = f"the solver stopped with {solver.modelStatusToString(status)}"
        return Answer(None, "fallback", None, runtime_s, detail)
    values = solver.getSolution().col_value
    chosen = frozenset(index for index, value in enumerate(values) if value > _TAKEN)
    return Answer(chosen, verdict, float(info.mip_dual_bound), runtime_s, None)

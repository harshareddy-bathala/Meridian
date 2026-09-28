"""The job that turns predicted passes into a schedule a station can be given.

Reads the passes generated over a horizon, values each under the configured
objective (D-168), and hands them to the optimiser (D-167), which takes the
selection of greatest total value that every station's antenna and delivery cap
allow (D-166). It writes the run and every decision, taken or skipped, each
with its explanation (D-170), in one transaction.

Nothing here reaches a network, and it never reads the observation store — what
a station *did* reaches a schedule only as a model's probability, which reads a
labelled dataset (D-169, ``docs/ARCHITECTURE.md``).

Running it twice over one horizon writes nothing the second time: a pass this
configuration has closed is not a candidate again, a skip decided again for the
same reason is not written again (D-165, D-171), and each decision's id is
derived from its pass, its configuration and its revision, so a race between
two runs still collapses onto ``assignment_decision_unique`` (D-066). A pass new
to a later run is decided around the assignments already made, never on top of
one. An offline station's work not yet begun is revoked, and decided again
when it returns (D-171).

Reference: docs/DECISIONS.md D-065, D-066, D-165 to D-171.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, require_utc
from meridian.prediction.live import LiveScorer
from meridian.scheduler import Candidate, Commitment
from meridian.scheduler.assignment_records import PassFacts, Stamp, to_assignment_rows
from meridian.scheduler.candidates import (
    ScheduleRequest,
    element_set_for,
    is_available,
    load_catalogue,
    work_for_station,
)
from meridian.scheduler.constraints import Problem, Rules, Violation, violations
from meridian.scheduler.explanations import RunFacts, explain
from meridian.scheduler.live_inputs import live_inputs
from meridian.scheduler.objective import ELEVATION_PROXY, MODEL, Yield, value_candidates
from meridian.scheduler.optimiser import (
    Optimised,
    SolverRun,
    SolverSettings,
    optimise,
)
from meridian.scheduler.reissue import unchanged
from meridian.scheduler.schedule_config import check_model, schedule_config_sha256
from meridian.scheduler.scoring import yields_of
from meridian.store.receiving_stations import find_receiving_stations
from meridian.store.revocations import revoke_offline
from meridian.store.schedule_reads import LatestDecision
from meridian.store.schedule_writes import NewScheduleRun, insert_schedule
from meridian.store.stations import Connection

__all__ = [
    "ScheduleInvalidError",
    "ScheduleReport",
    "ScheduleRequest",
    "run_schedule",
]

RUN_ID_PREFIX = "sr_"


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    """What one run decided, in enough detail to explain an empty schedule."""

    model_config: str
    stations_considered: int
    candidates_considered: int
    scheduled: int
    skipped: int
    rows_written: int
    """Rows that landed. Normally ``scheduled + skipped``; fewer only when
    another run decided some of the same passes in between."""

    already_decided: int
    """Passes in the horizon this configuration had decided in an earlier run,
    and so did not consider — on a re-run over an unchanged horizon, all of
    them (D-165)."""

    passes_without_a_usable_transmitter: tuple[int, ...]
    """Passes dropped before valuing because no live downlink of that satellite
    matches the station's declared hardware.

    Normally empty: pass generation applies the same test (D-064). It fills when
    the catalogue changed between the two runs — a transmitter switched off, a
    capability withdrawn — and naming the passes is what distinguishes that from
    a satellite that simply never rose.
    """

    stations_unavailable: tuple[str, ...]
    """Stations ``offline`` at ``request.now``, given nothing new (D-166)."""

    passes_deferred: int
    """Their open passes, left undecided rather than skipped, so a round after
    the station returns decides them."""

    yield_source: str
    """``model`` or ``elevation_proxy`` (D-168)."""

    revoked: int = 0
    """Their assignments not yet begun, taken back (D-171)."""

    unchanged: int = 0
    """Skips decided again for the same reason, and so not written (D-171)."""

    run_id: str | None = None
    """The recorded run; ``None`` when it wrote no decision."""

    solver: SolverRun | None = None
    history_as_of: datetime | None = None
    """How recent the history the model read was; ``None`` without one."""


class ScheduleInvalidError(RuntimeError):
    """A schedule broke a constraint, so none of the run was written (D-166)."""

    def __init__(self, found: Sequence[Violation]) -> None:
        """Name every broken rule and the passes that broke it."""
        self.violations = tuple(found)
        named = "; ".join(f"{one.rule} {list(one.pass_ids)}" for one in found)
        super().__init__(f"the schedule breaks its constraints: {named}")


@dataclass(slots=True)
class _Gathered:
    """Every available station's candidates, and what the run needs about them."""

    candidates: list[Candidate] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    facts: dict[int, PassFacts] = field(default_factory=dict)
    yields: dict[int, Yield] = field(default_factory=dict)
    stations: int = 0
    already_decided: int = 0
    unusable: list[int] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    deferred: int = 0
    revoked: int = 0
    revisions: dict[int, int] = field(default_factory=dict)
    previous: dict[int, LatestDecision] = field(default_factory=dict)


def _gather(
    conn: Connection,
    orbit: OrbitService,
    request: ScheduleRequest,
    scorer: LiveScorer | None,
) -> _Gathered:
    """Read every station's candidates, and score them where a model is configured."""
    catalogue = load_catalogue(conn)
    gathered = _Gathered()
    element_sets: dict[int, ElementSet] = {}

    def element_set(set_id: int) -> ElementSet:
        if set_id not in element_sets:
            element_sets[set_id] = element_set_for(conn, set_id)
        return element_sets[set_id]

    for station in find_receiving_stations(conn):
        gathered.stations += 1
        work = work_for_station(conn, orbit, station, catalogue, request)
        gathered.unusable.extend(work.passes_without_a_usable_transmitter)
        gathered.already_decided += work.already_decided
        if not is_available(conn, station.station_id, request.now):
            gathered.unavailable.append(station.station_id)
            gathered.deferred += len(work.candidates)
            gathered.revoked += revoke_offline(
                conn, station.station_id, now=request.now
            )
            continue
        gathered.candidates.extend(work.candidates)
        gathered.commitments.extend(work.commitments)
        gathered.facts.update(work.facts_by_pass_id)
        gathered.revisions.update(work.revisions)
        gathered.previous.update(work.previous)
        if scorer is not None and work.candidates:
            passes, geometry = live_inputs(
                orbit,
                station,
                work.stored,
                [one.pass_id for one in work.candidates],
                element_set,
            )
            gathered.yields.update(yields_of(scorer.score(passes, geometry)))
    return gathered


def run_schedule(
    conn: Connection,
    orbit: OrbitService,
    request: ScheduleRequest,
    scorer: LiveScorer | None = None,
) -> ScheduleReport:
    """Schedule every station's passes over a horizon under one configuration.

    Args:
        conn: An open connection. The run and all its decisions are written in
            one transaction — a half-written schedule is not a partial result
            but a wrong one, because its rows reference each other.
        orbit: The propagator: timing uncertainty, and a model's tracks.
        request: The horizon, the instant and the schedule configuration.
        scorer: The configured model, loaded; ``None`` when none is configured.

    Returns:
        A :class:`ScheduleReport` describing what was decided and what landed.

    Raises:
        ValueError: A time is naive or not UTC, or the scorer does not match
            the configuration: given where none is configured, missing where
            one is, or another configuration's model.
        LookupError: A stored pass references an element set that is gone.
        ScheduleInvalidError: The schedule broke a constraint. Nothing is
            written: every schedule is checked by
            :func:`~meridian.scheduler.constraints.violations` before it is
            kept, however it was found (D-166).
    """
    require_utc(request.start, "request.start")
    require_utc(request.end, "request.end")
    require_utc(request.now, "request.now")
    config = request.config
    if (config.model is None) != (scorer is None):
        message = (
            "the scorer must be given exactly when the configuration names a"
            f" model; it names {config.model!r}"
        )
        raise ValueError(message)
    if scorer is not None:
        check_model(config, scorer.model.configuration)

    gathered = _gather(conn, orbit, request, scorer)
    report = ScheduleReport(
        model_config=config.configuration,
        stations_considered=gathered.stations,
        candidates_considered=len(gathered.candidates),
        scheduled=0,
        skipped=0,
        rows_written=0,
        passes_without_a_usable_transmitter=tuple(gathered.unusable),
        already_decided=gathered.already_decided,
        stations_unavailable=tuple(gathered.unavailable),
        passes_deferred=gathered.deferred,
        revoked=gathered.revoked,
        yield_source=ELEVATION_PROXY if scorer is None else MODEL,
        history_as_of=None
        if scorer is None or scorer.past is None
        else scorer.past.as_of,
    )
    if not gathered.candidates:
        return report
    optimised, stamp = _decide(request, gathered, scorer)
    outcome = optimised.outcome
    decided = to_assignment_rows(outcome, gathered.facts, config.configuration, stamp)
    rows = [
        row for row in decided if not unchanged(gathered.previous.get(row.pass_id), row)
    ]
    report = replace(
        report,
        scheduled=len(outcome.selected),
        skipped=len(outcome.rejected),
        unchanged=len(decided) - len(rows),
    )
    if not rows:
        # Everything decided again said what it said before: no run to record.
        return report
    run = _run_row(request, report, optimised, scorer, stamp.run_id)
    return replace(
        report,
        rows_written=insert_schedule(conn, run, rows),
        run_id=stamp.run_id,
        solver=optimised.run,
    )


def _decide(
    request: ScheduleRequest, gathered: _Gathered, scorer: LiveScorer | None
) -> tuple[Optimised, Stamp]:
    """Value, optimise and check the schedule, and explain every decision."""
    config = request.config
    scored, terms = value_candidates(
        gathered.candidates,
        configuration=config.configuration,
        frames_term=config.frames,
        yields=None if scorer is None else gathered.yields,
    )
    rules = Rules(turnaround_s=request.turnaround_s)
    optimised = optimise(
        scored,
        rules=rules,
        settings=SolverSettings(time_limit_s=config.time_limit_s, seed=config.seed),
        committed=gathered.commitments,
    )
    problem = Problem(
        candidates=tuple(gathered.candidates),
        commitments=tuple(gathered.commitments),
        unavailable=frozenset(),
        rules=rules,
    )
    found = violations(problem, optimised.outcome)
    if found:
        raise ScheduleInvalidError(found)
    history_as_of = None if scorer is None or scorer.past is None else scorer.past.as_of
    stamp = Stamp(
        run_id=f"{RUN_ID_PREFIX}{uuid.uuid4().hex[:12]}",
        model_sha256=None if scorer is None else scorer.model_sha256,
        explanations=explain(
            optimised.outcome,
            terms,
            gathered.commitments,
            turnaround_s=request.turnaround_s,
            run=RunFacts(status=optimised.run.status, history_as_of=history_as_of),
        ),
        predicted_yields={
            pass_id: one.probability for pass_id, one in gathered.yields.items()
        },
        revisions=gathered.revisions,
    )
    return optimised, stamp


def _run_row(
    request: ScheduleRequest,
    report: ScheduleReport,
    optimised: Optimised,
    scorer: LiveScorer | None,
    run_id: str,
) -> NewScheduleRun:
    """The run as ``schedule_runs`` records it."""
    config = request.config
    solver = optimised.run
    past = None if scorer is None else scorer.past
    return NewScheduleRun(
        run_id=run_id,
        decided_at=request.now,
        horizon_start=request.start,
        horizon_end=request.end,
        model_config=config.configuration,
        config_sha256=schedule_config_sha256(config),
        parameters=config.parameters(),
        yield_source=report.yield_source,
        model_sha256=None if scorer is None else scorer.model_sha256,
        history_sha256=None if past is None else past.dataset_sha256,
        history_as_of=None if past is None else past.as_of,
        solver=solver.solver,
        solver_version=solver.version,
        status=solver.status,
        objective=solver.objective,
        bound=solver.bound,
        time_limit_s=solver.time_limit_s,
        runtime_s=solver.runtime_s,
        detail=solver.detail,
        stations=report.stations_considered - len(report.stations_unavailable),
        candidates=report.candidates_considered,
        scheduled=len(optimised.outcome.selected),
        skipped=len(optimised.outcome.rejected),
    )

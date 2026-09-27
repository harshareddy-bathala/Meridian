"""The job that turns predicted passes into a schedule a station can be given.

Reads the passes generated over a horizon, ranks them under one of
docs/EVALUATION.md §3's configurations, takes as many as each station's antenna
allows, and writes both the selections and the skips to ``assignments``.

This is where Phase 1's operational path closes: ``pass_generation`` says what is
possible, and this says what will be attempted. Nothing here reaches a network,
and it never reads the observation store — what a station *did* is not an input
to what it should be asked to do next (docs/ARCHITECTURE.md).

I/O is confined to the ``_load_*`` functions and ``insert_assignments``. The
ranking, the non-overlap rule and the row building are pure modules beside this
one, so what the scheduler decides is testable without a database.

Running it twice over one horizon writes nothing the second time: a pass this
configuration has already decided is not a candidate again (D-165), and each
decision's id is derived from its pass and its configuration, so a race between
two runs still collapses onto ``assignment_decision_unique`` (D-066). A pass new
to a later run — the tail of an overlapping horizon, or a newer element set's
prediction — is decided around the assignments already made, never on top of
one.

Reference: docs/EVALUATION.md §3; docs/DECISIONS.md D-021, D-065, D-066, D-165.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from meridian.orbit.service import OrbitService
from meridian.orbit.types import require_utc
from meridian.scheduler import Candidate, ScoredCandidate
from meridian.scheduler.assignment_records import to_assignment_rows
from meridian.scheduler.candidates import (
    ScheduleRequest,
    StationWork,
    is_available,
    load_catalogue,
    work_for_station,
)
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.constraints import Problem, Rules, Violation, violations
from meridian.scheduler.elevation_baseline import rank_by_elevation
from meridian.scheduler.priority_baseline import rank_by_priority_weighted_elevation
from meridian.store.assignments import NewAssignment, insert_assignments
from meridian.store.receiving_stations import find_receiving_stations
from meridian.store.stations import Connection

__all__ = [
    "RANKERS",
    "ScheduleInvalidError",
    "ScheduleReport",
    "ScheduleRequest",
    "run_schedule",
]

Ranker = Callable[[Sequence[Candidate]], list[ScoredCandidate]]

RANKERS: dict[str, Ranker] = {
    "A": rank_by_elevation,
    "B": rank_by_priority_weighted_elevation,
}
"""The configurations this stage implements, selectable by flag.

docs/EVALUATION.md §3 requires any configuration to be runnable by config flag,
and names four. C and D are learned models and arrive at Stage 17; they join
this table rather than replacing it, so the same run, the same non-overlap rule
and the same row building serve all four and nothing but the ranking differs
between the numbers eventually reported.
"""


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
    """Passes dropped before ranking because no live downlink of that satellite
    matches the station's declared hardware.

    Normally empty: pass generation applies the same test (D-064). It fills when
    the catalogue changed between the two runs — a transmitter switched off, a
    capability withdrawn — and naming the passes is what distinguishes that from
    a satellite that simply never rose.
    """

    stations_unavailable: tuple[str, ...]
    """Stations ``offline`` at ``request.now``, given nothing new (D-166)."""

    passes_deferred: int
    """Their undecided passes, left undecided rather than skipped, so a round
    after the station returns decides them."""


class ScheduleInvalidError(RuntimeError):
    """A schedule broke a constraint, so none of the run was written (D-166)."""

    def __init__(self, found: Sequence[Violation]) -> None:
        """Name every broken rule and the passes that broke it."""
        self.violations = tuple(found)
        named = "; ".join(f"{one.rule} {list(one.pass_ids)}" for one in found)
        super().__init__(f"the schedule breaks its constraints: {named}")


@dataclass(slots=True)
class _Tally:
    """What the run has decided so far, across stations."""

    rows: list[NewAssignment] = field(default_factory=list)
    considered: int = 0
    scheduled: int = 0
    already_decided: int = 0
    unusable: list[int] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    deferred: int = 0


def _schedule_station(
    work: StationWork, ranker: Ranker, request: ScheduleRequest, tally: _Tally
) -> None:
    """Decide one available station's candidates, checked before they are kept."""
    rules = Rules(turnaround_s=request.turnaround_s)
    outcome = select_without_conflict(
        ranker(work.candidates), rules=rules, committed=work.commitments
    )
    problem = Problem(
        candidates=tuple(work.candidates),
        commitments=tuple(work.commitments),
        unavailable=frozenset(),
        rules=rules,
    )
    found = violations(problem, outcome)
    if found:
        raise ScheduleInvalidError(found)
    tally.considered += len(work.candidates)
    tally.scheduled += len(outcome.selected)
    tally.rows.extend(
        to_assignment_rows(outcome, work.facts_by_pass_id, request.model_config)
    )


def run_schedule(
    conn: Connection, orbit: OrbitService, request: ScheduleRequest
) -> ScheduleReport:
    """Schedule every station's passes over a horizon under one configuration.

    Args:
        conn: An open connection. Every decision from the run is written in one
            transaction — unlike pass generation, a half-written schedule is not
            a partial result but a wrong one, because its rows reference each
            other.
        orbit: The propagator, used only to state how confident the platform is
            in each pass's boundaries. Injected so the scheduling decisions can
            be exercised against a stub.
        request: The horizon, the configuration, and the station turnaround.

    Returns:
        A :class:`ScheduleReport` describing what was decided and what landed.

    Raises:
        ValueError: The horizon is naive or not UTC, or ``model_config`` names
            a configuration this stage does not implement. Naming C or D today
            fails loudly rather than silently falling back to A, which would
            publish a number under a label it did not earn.
        LookupError: A stored pass references an element set that is gone.
        ScheduleInvalidError: The schedule broke a constraint. Nothing is
            written: every schedule is checked by
            :func:`~meridian.scheduler.constraints.violations` before it is
            kept, however it was found (D-166).

    Note:
        **Re-running over one horizon writes nothing.** A pass this
        configuration has decided is not considered again (D-165), and the
        decision ids collapse onto ``assignment_decision_unique`` besides
        (D-066). A *different* configuration over the same horizon decides the
        same passes again, around the first one's assignments: a station has
        one antenna, and configurations are compared by replay (D-172), not by
        delivering two schedules to it.
    """
    require_utc(request.start, "request.start")
    require_utc(request.end, "request.end")
    require_utc(request.now, "request.now")

    ranker = RANKERS.get(request.model_config)
    if ranker is None:
        raise ValueError(
            f"no ranking for configuration {request.model_config!r}; "
            f"this stage implements {sorted(RANKERS)}"
        )

    catalogue = load_catalogue(conn)
    stations = find_receiving_stations(conn)
    tally = _Tally()

    for station in stations:
        work = work_for_station(conn, orbit, station, catalogue, request)
        tally.unusable.extend(work.passes_without_a_usable_transmitter)
        tally.already_decided += work.already_decided
        if is_available(conn, station.station_id, request.now):
            _schedule_station(work, ranker, request, tally)
        else:
            tally.unavailable.append(station.station_id)
            tally.deferred += len(work.candidates)

    return ScheduleReport(
        model_config=request.model_config,
        stations_considered=len(stations),
        candidates_considered=tally.considered,
        scheduled=tally.scheduled,
        skipped=tally.considered - tally.scheduled,
        rows_written=insert_assignments(conn, tally.rows),
        passes_without_a_usable_transmitter=tuple(tally.unusable),
        already_decided=tally.already_decided,
        stations_unavailable=tuple(tally.unavailable),
        passes_deferred=tally.deferred,
    )

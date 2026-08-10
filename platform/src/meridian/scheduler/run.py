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

Running it twice over one horizon writes nothing the second time: each decision's
id is derived from its pass and its configuration, so the repeat collapses onto
``assignment_decision_unique`` (D-066).

Reference: docs/EVALUATION.md §3; docs/DECISIONS.md D-021, D-065, D-066.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, require_utc
from meridian.registry.capability_match import (
    ReceiveCapability,
    covers_transmission,
)
from meridian.scheduler import Candidate, ScoredCandidate
from meridian.scheduler.assignment_records import PassFacts, to_assignment_rows
from meridian.scheduler.conflict_rejection import select_without_conflict
from meridian.scheduler.elevation_baseline import rank_by_elevation
from meridian.scheduler.priority_baseline import (
    NEUTRAL_PRIORITY,
    rank_by_priority_weighted_elevation,
)
from meridian.store.assignments import insert_assignments
from meridian.store.element_sets import find_element_set_by_id
from meridian.store.passes import StoredPass, find_passes_in_horizon
from meridian.store.satellites import (
    StoredTransmitter,
    find_active_transmitters,
    find_satellite_priorities,
)
from meridian.store.station_capabilities import find_capabilities_for_station
from meridian.store.stations import (
    Connection,
    ReceivingStation,
    find_receiving_stations,
)

__all__ = [
    "RANKERS",
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
class ScheduleRequest:
    """One scheduling run: over what, under which configuration, for what hardware."""

    start: datetime
    end: datetime
    """Half-open on acquisition, matching ``find_passes_in_horizon`` — a pass
    belongs to the horizon it rises in (D-059)."""

    model_config: str
    """``A`` or ``B``. Recorded on every row so a schedule can be attributed."""

    turnaround_s: float
    """Seconds a station needs between two receptions, for slew and settling.

    One value for the run. Phase 1's stations receive on a fixed QFH antenna,
    which does not slew, so the honest value is zero — see D-066 for what has to
    change before a tracking station can be scheduled correctly.
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
    """Rows that landed. ``scheduled + skipped - rows_written`` were already
    stored — on a re-run over an unchanged horizon that is the whole total."""

    passes_without_a_usable_transmitter: tuple[int, ...]
    """Passes dropped before ranking because no live downlink of that satellite
    matches the station's declared hardware.

    Normally empty: pass generation applies the same test (D-064). It fills when
    the catalogue changed between the two runs — a transmitter switched off, a
    capability withdrawn — and naming the passes is what distinguishes that from
    a satellite that simply never rose.
    """


def _load_capabilities(conn: Connection, station_id: str) -> list[ReceiveCapability]:
    """One station's declared receiving chains, in the matcher's shape."""
    return [
        ReceiveCapability(
            freq_min_hz=stored.freq_min_hz,
            freq_max_hz=stored.freq_max_hz,
            modes=tuple(stored.modes),
            min_elevation_deg=stored.min_elevation_deg,
        )
        for stored in find_capabilities_for_station(conn, station_id)
    ]


def _first_receivable_transmitter(
    transmitters: Sequence[StoredTransmitter],
    capabilities: Sequence[ReceiveCapability],
) -> StoredTransmitter | None:
    """The downlink this station will be pointed at, or None if none matches.

    The first in the catalogue's deterministic order — by frequency, then by id
    — rather than the best. Which of a satellite's downlinks is worth more is a
    prediction question about expected yield, and Phase 1 has no model to answer
    it with; picking arbitrarily but *reproducibly* is the honest placeholder,
    and it is visible in ``assignments.centre_freq_hz`` rather than hidden.
    """
    for transmitter in transmitters:
        if any(
            covers_transmission(
                capability, transmitter.centre_freq_hz, transmitter.mode
            )
            for capability in capabilities
        ):
            return transmitter
    return None


def _timing_uncertainty_s(
    orbit: OrbitService, stored: StoredPass, element_set: ElementSet
) -> float:
    """The platform's 1σ confidence in this pass's boundaries, at its rise."""
    return orbit.timing_uncertainty(element_set, stored.aos).sigma_s


def _element_set_for(conn: Connection, element_set_id: int) -> ElementSet:
    """The archived set a stored pass was computed from.

    Read back by id rather than by "which set is current": the pass names the
    exact set that produced it, so widening its window uses that set's age and
    not whatever has arrived since.
    """
    stored = find_element_set_by_id(conn, element_set_id)
    if stored is None:
        raise LookupError(
            f"pass references element set {element_set_id}, which is gone"
        )

    return ElementSet(
        satellite_id=stored.satellite_id,
        epoch=stored.epoch,
        line1=stored.line1,
        line2=stored.line2,
        source=stored.source,
    )


@dataclass(frozen=True, slots=True)
class _Catalogue:
    """What is tracked, loaded once for the whole run.

    Both maps are properties of the satellite rather than of any station, so
    re-reading them inside the station loop would make the run's cost quadratic
    in the network without changing a single decision.
    """

    transmitters_by_satellite: dict[str, list[StoredTransmitter]]
    priorities: dict[str, float]


@dataclass(frozen=True, slots=True)
class _StationWork:
    """One station's candidates, and the facts each decision will need."""

    candidates: list[Candidate]
    facts_by_pass_id: dict[int, PassFacts]
    passes_without_a_usable_transmitter: list[int]


def _load_catalogue(conn: Connection) -> _Catalogue:
    """Read the live downlinks and the operator weightings."""
    transmitters_by_satellite: dict[str, list[StoredTransmitter]] = {}
    for transmitter in find_active_transmitters(conn):
        group = transmitters_by_satellite.setdefault(transmitter.satellite_id, [])
        group.append(transmitter)

    return _Catalogue(
        transmitters_by_satellite=transmitters_by_satellite,
        priorities=find_satellite_priorities(conn),
    )


def _candidate_from(stored: StoredPass, priority: float) -> Candidate:
    """One stored pass as the scheduler sees it.

    ``simulated`` is carried from the pass, which carried it from the station
    (D-013), so the assignment written at the end inherits it rather than
    defaulting to false.
    """
    return Candidate(
        pass_id=stored.id,
        station_id=stored.station_id,
        aos=stored.aos,
        los=stored.los,
        max_elevation_deg=stored.max_elevation_deg,
        priority=priority,
        simulated=stored.simulated,
    )


def _facts_from(
    stored: StoredPass, transmitter: StoredTransmitter, timing_uncertainty_s: float
) -> PassFacts:
    """What the row for this pass needs beyond the candidate itself."""
    return PassFacts(
        centre_freq_hz=transmitter.centre_freq_hz,
        mode=transmitter.mode,
        aos=stored.aos,
        los=stored.los,
        timing_uncertainty_s=timing_uncertainty_s,
    )


def _work_for_station(
    conn: Connection,
    orbit: OrbitService,
    station: ReceivingStation,
    catalogue: _Catalogue,
    request: ScheduleRequest,
) -> _StationWork:
    """Gather one station's candidates over the horizon, with their facts."""
    capabilities = _load_capabilities(conn, station.station_id)
    candidates: list[Candidate] = []
    facts_by_pass_id: dict[int, PassFacts] = {}
    unusable: list[int] = []

    for stored in find_passes_in_horizon(
        conn, station.station_id, request.start, request.end
    ):
        transmitter = _first_receivable_transmitter(
            catalogue.transmitters_by_satellite.get(stored.satellite_id, ()),
            capabilities,
        )
        if transmitter is None:
            unusable.append(stored.id)
            continue

        element_set = _element_set_for(conn, stored.element_set_id)
        candidates.append(
            _candidate_from(
                stored,
                catalogue.priorities.get(stored.satellite_id, NEUTRAL_PRIORITY),
            )
        )
        facts_by_pass_id[stored.id] = _facts_from(
            stored, transmitter, _timing_uncertainty_s(orbit, stored, element_set)
        )

    return _StationWork(
        candidates=candidates,
        facts_by_pass_id=facts_by_pass_id,
        passes_without_a_usable_transmitter=unusable,
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

    Note:
        **Re-running over one horizon writes nothing.** Each decision's id is
        derived from its pass and its configuration, so the repeat collapses
        onto ``assignment_decision_unique`` (D-066). Running a *different*
        configuration over the same horizon does write, which is what the
        ablation in docs/EVALUATION.md §3 needs — A's schedule and B's coexist
        and are compared.
    """
    require_utc(request.start, "request.start")
    require_utc(request.end, "request.end")

    ranker = RANKERS.get(request.model_config)
    if ranker is None:
        raise ValueError(
            f"no ranking for configuration {request.model_config!r}; "
            f"this stage implements {sorted(RANKERS)}"
        )

    catalogue = _load_catalogue(conn)
    stations = find_receiving_stations(conn)

    rows = []
    considered = 0
    scheduled = 0
    unusable: list[int] = []

    for station in stations:
        work = _work_for_station(conn, orbit, station, catalogue, request)
        unusable.extend(work.passes_without_a_usable_transmitter)
        considered += len(work.candidates)

        outcome = select_without_conflict(
            ranker(work.candidates), turnaround_s=request.turnaround_s
        )
        scheduled += len(outcome.selected)
        rows.extend(
            to_assignment_rows(outcome, work.facts_by_pass_id, request.model_config)
        )

    return ScheduleReport(
        model_config=request.model_config,
        stations_considered=len(stations),
        candidates_considered=considered,
        scheduled=scheduled,
        skipped=considered - scheduled,
        rows_written=insert_assignments(conn, rows),
        passes_without_a_usable_transmitter=tuple(unusable),
    )

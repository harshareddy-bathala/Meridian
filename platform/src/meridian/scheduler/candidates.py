"""Gathering one station's candidates for a scheduler run, and what binds them.

For each station the run needs three things from the database:

* the passes rising in the horizon that are open under this configuration —
  never decided, skipped, or revoked while their station was offline (D-171)
  — as candidates, each with the downlink and the timing uncertainty its row
  will carry, and the revision its decision will be written as. A pass whose
  track never clears the station's declared horizon is not a candidate (D-175);
* how many are closed, so a report can say why a repeat wrote nothing; and
* the assignments the station is already committed to near those passes, which
  no decision of this run may overlap (D-165).

Split out of ``run`` so that module holds the run and this one holds the reads.
Nothing here decides anything.

Reference: docs/DECISIONS.md D-021, D-060, D-064, D-066, D-165, D-175.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.registry.capability_match import (
    ReceiveCapability,
    covers_transmission,
)
from meridian.registry.liveness import derive_liveness
from meridian.scheduler import Candidate, Commitment
from meridian.scheduler.assignment_records import PassFacts
from meridian.scheduler.constraints import DELIVERY_LEAD, window
from meridian.scheduler.declared_horizon import DeclaredMask, clears_any
from meridian.scheduler.live_inputs import TRACK_STEP_S
from meridian.scheduler.priority_baseline import NEUTRAL_PRIORITY
from meridian.scheduler.reissue import next_revision, reopens
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.element_sets import find_element_set_by_id
from meridian.store.passes import StoredPass, find_passes_in_horizon
from meridian.store.receiving_stations import ReceivingStation
from meridian.store.satellites import (
    StoredTransmitter,
    find_active_transmitters,
    find_satellite_priorities,
)
from meridian.store.schedule_reads import (
    LatestDecision,
    StoredCommitment,
    find_commitments,
    find_latest_decisions,
)
from meridian.store.station_capabilities import find_capabilities_for_station
from meridian.store.stations import Connection, find_station_heartbeat

__all__ = [
    "Catalogue",
    "ScheduleRequest",
    "StationWork",
    "element_set_for",
    "is_available",
    "load_catalogue",
    "work_for_station",
]


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    """One scheduling run: over what, when, and under which configuration."""

    start: datetime
    end: datetime
    """Half-open on acquisition, matching ``find_passes_in_horizon`` — a pass
    belongs to the horizon it rises in (D-059)."""

    now: datetime
    """When the run is made, timezone-aware UTC: the instant each station's
    liveness is judged at (D-166). Passed rather than read, as ``start`` is,
    so a run can be stated exactly."""

    config: ScheduleConfig
    """The configuration, the model, the objective's terms, the solver's limit
    and the station turnaround (D-168)."""

    @property
    def model_config(self) -> str:
        """``A`` to ``D``, recorded on every row so a schedule can be attributed."""
        return self.config.configuration

    @property
    def turnaround_s(self) -> float:
        """Seconds a station needs between two receptions (D-066).

        One value for the run. Phase 1's stations receive on a fixed QFH
        antenna, which does not slew, so the honest value is zero — see D-066
        for what has to change before a tracking station can be scheduled
        correctly.
        """
        return float(self.config.turnaround_s)


@dataclass(frozen=True, slots=True)
class Catalogue:
    """What is tracked, loaded once for the whole run.

    Both maps are properties of the satellite rather than of any station, so
    re-reading them inside the station loop would make the run's cost quadratic
    in the network without changing a single decision.
    """

    transmitters_by_satellite: dict[str, list[StoredTransmitter]]
    priorities: dict[str, float]


@dataclass(frozen=True, slots=True)
class StationWork:
    """One station's candidates, the facts each decision will need, and its ties."""

    candidates: list[Candidate]
    facts_by_pass_id: dict[int, PassFacts]
    passes_without_a_usable_transmitter: list[int]
    passes_below_the_declared_horizon: list[int]
    """Left undecided rather than skipped: a skip is final (D-165), and a mask
    an operator corrects should give these passes back (D-175)."""
    already_decided: int
    """Passes in the horizon this configuration has closed: held, done or
    declined (D-171)."""

    revisions: dict[int, int]
    """By candidate, the revision its decision will be written as."""

    previous: dict[int, LatestDecision]
    """By candidate decided before, that decision, so one saying nothing new is
    not written again (D-171)."""

    commitments: list[Commitment]
    """Open assignments near the candidates, of any configuration."""

    stored: dict[int, StoredPass]
    """Every prediction rising in the horizon for this station, decided or
    not: a candidate's rise is read from them (D-148, D-169)."""


def _load_capabilities(
    conn: Connection, station_id: str
) -> list[tuple[ReceiveCapability, DeclaredMask]]:
    """One station's declared receiving chains, each with its declared mask."""
    return [
        (
            ReceiveCapability(
                freq_min_hz=stored.freq_min_hz,
                freq_max_hz=stored.freq_max_hz,
                modes=tuple(stored.modes),
                min_elevation_deg=stored.min_elevation_deg,
            ),
            DeclaredMask.from_stored(stored.horizon_mask),
        )
        for stored in find_capabilities_for_station(conn, station_id)
    ]


def _clears_declared_horizon(  # noqa: PLR0913 — each is one fact the test needs
    orbit: OrbitService,
    *,
    element_set: ElementSet,
    site: GroundSite,
    stored: StoredPass,
    transmitter: StoredTransmitter,
    chains: Sequence[tuple[ReceiveCapability, DeclaredMask]],
) -> bool:
    """Whether a chain that can receive this downlink sees the pass over its mask.

    The track is sampled as live scoring samples it (D-169). It is computed only
    when every covering chain declares a mask, so a station that declares none
    propagates nothing more than it did before (D-175).
    """
    masks = [
        mask
        for capability, mask in chains
        if covers_transmission(capability, transmitter.centre_freq_hz, transmitter.mode)
    ]
    if not masks or any(mask.empty for mask in masks):
        return True
    angles = orbit.look_angles(
        element_set, site, stored.aos, stored.los, step_s=TRACK_STEP_S
    )
    track = [(one.azimuth_deg % 360.0, one.elevation_deg) for one in angles]
    return clears_any(masks, track)


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


def element_set_for(conn: Connection, element_set_id: int) -> ElementSet:
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


def is_available(conn: Connection, station_id: str, now: datetime) -> bool:
    """Whether a station may be given new work: not ``offline`` (D-166).

    **A station never seen is available.** It has registered and not yet
    reported, which is what every station is between registration and its
    first heartbeat, and that heartbeat is what delivers its assignments. A
    ``stale`` one has missed two heartbeats and is still inside SC-5's 90 s.
    Only ``offline`` — the registry's judgement that it stopped — withholds
    work.
    """
    heartbeat = find_station_heartbeat(conn, station_id)
    last = None if heartbeat is None else heartbeat.last_heartbeat_at
    return derive_liveness(last, now=now) != "offline"


def load_catalogue(conn: Connection) -> Catalogue:
    """Read the live downlinks and the operator weightings."""
    transmitters_by_satellite: dict[str, list[StoredTransmitter]] = {}
    for transmitter in find_active_transmitters(conn):
        group = transmitters_by_satellite.setdefault(transmitter.satellite_id, [])
        group.append(transmitter)

    return Catalogue(
        transmitters_by_satellite=transmitters_by_satellite,
        priorities=find_satellite_priorities(conn),
    )


def _candidate_from(
    stored: StoredPass, priority: float, timing_uncertainty_s: float
) -> Candidate:
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
        margin_s=timing_uncertainty_s,
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


def _commitment_from(stored: StoredCommitment) -> Commitment:
    """A stored assignment as a fixed point of this run."""
    return Commitment(
        candidate=Candidate(
            pass_id=stored.pass_id,
            station_id=stored.station_id,
            aos=stored.aos,
            los=stored.los,
            margin_s=stored.timing_uncertainty_s,
            max_elevation_deg=stored.max_elevation_deg,
            priority=stored.priority,
            simulated=stored.simulated,
        ),
        assignment_id=stored.assignment_id,
    )


def _commitments_near(
    conn: Connection, candidates: Sequence[Candidate], turnaround_s: float
) -> list[Commitment]:
    """The open assignments any of ``candidates`` could collide with.

    Bounded by the candidates' own windows, opened out by the turnaround and
    by how far ahead a heartbeat delivers, not by the horizon: a pass rising
    just before the horizon may still be under way when the first candidate
    rises, and one two hours before it still counts against the delivery cap
    (D-166).
    """
    if not candidates:
        return []
    room = timedelta(seconds=turnaround_s)
    windows = [window(one) for one in candidates]
    return [
        _commitment_from(stored)
        for stored in find_commitments(
            conn,
            candidates[0].station_id,
            min(start for start, _ in windows) - room - DELIVERY_LEAD,
            max(end for _, end in windows) + room + DELIVERY_LEAD,
        )
    ]


def work_for_station(
    conn: Connection,
    orbit: OrbitService,
    station: ReceivingStation,
    catalogue: Catalogue,
    request: ScheduleRequest,
) -> StationWork:
    """Gather one station's open candidates over the horizon, with their ties."""
    chains = _load_capabilities(conn, station.station_id)
    capabilities = [capability for capability, _ in chains]
    site = GroundSite(
        lat_deg=station.lat_deg, lon_deg=station.lon_deg, alt_m=station.alt_m
    )
    stored_passes = find_passes_in_horizon(
        conn, station.station_id, request.start, request.end
    )
    latest = find_latest_decisions(
        conn, [stored.id for stored in stored_passes], request.model_config
    )
    closed = {pass_id for pass_id, held in latest.items() if not reopens(held)}
    candidates: list[Candidate] = []
    facts_by_pass_id: dict[int, PassFacts] = {}
    unusable: list[int] = []
    masked: list[int] = []

    for stored in stored_passes:
        if stored.id in closed:
            continue
        transmitter = _first_receivable_transmitter(
            catalogue.transmitters_by_satellite.get(stored.satellite_id, ()),
            capabilities,
        )
        if transmitter is None:
            unusable.append(stored.id)
            continue

        element_set = element_set_for(conn, stored.element_set_id)
        if not _clears_declared_horizon(
            orbit,
            element_set=element_set,
            site=site,
            stored=stored,
            transmitter=transmitter,
            chains=chains,
        ):
            masked.append(stored.id)
            continue
        margin_s = _timing_uncertainty_s(orbit, stored, element_set)
        candidates.append(
            _candidate_from(
                stored,
                catalogue.priorities.get(stored.satellite_id, NEUTRAL_PRIORITY),
                margin_s,
            )
        )
        facts_by_pass_id[stored.id] = _facts_from(stored, transmitter, margin_s)

    return StationWork(
        candidates=candidates,
        facts_by_pass_id=facts_by_pass_id,
        passes_without_a_usable_transmitter=unusable,
        passes_below_the_declared_horizon=masked,
        already_decided=len(closed),
        revisions={
            one.pass_id: next_revision(latest.get(one.pass_id)) for one in candidates
        },
        previous={
            one.pass_id: latest[one.pass_id]
            for one in candidates
            if one.pass_id in latest
        },
        commitments=_commitments_near(conn, candidates, request.turnaround_s),
        stored={stored.id: stored for stored in stored_passes},
    )

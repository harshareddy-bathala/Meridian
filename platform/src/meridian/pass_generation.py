"""The job that fills the ``passes`` table from local element sets.

Given a horizon, it asks: which registered stations could receive which tracked
satellite, what does the archive say that satellite was doing, and when does the
geometry put it over each station? Every answer it stores is a row in
``passes`` — an opportunity that existed whether or not anyone took it, which is
what makes the table usable as the completeness denominator in
docs/EVALUATION.md §4.1.

This is the first module to join the three halves of Phase 1: it reads the
registry's stations through ``meridian.store``, decides receivability with
``meridian.registry.capability_match``, and propagates with an
``OrbitService``. It sits above all three and none of them knows it exists.

**Nothing here reaches a network.** The element sets come from the archive, the
propagator is local, and the job runs unchanged with every external service
offline — the independence test in CLAUDE.md, applied to the one job that would
otherwise be tempted to fetch fresh elements.

I/O is confined to the three functions named ``_load_*`` and ``_store_passes``;
choosing what to compute is pure and takes plain values, so the decision that
one station gets a pass and another does not can be tested without a database.

Running it twice over one horizon stores nothing the second time. That is not a
convenience — it is what makes the job safe on a timer and safe to re-run after
a crash, and it rests on the pass identity fixed by D-063 together with the
aligned scan grid that makes a computed acquisition reproducible.

Reference: docs/DECISIONS.md D-013 (provenance is the station's), D-059 (a pass
belongs to the search it rises in), D-063 (pass identity), D-064 (what this job
skips, and why it says so).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian.orbit.service import OrbitService
from meridian.orbit.types import (
    ElementSet,
    GroundSite,
    PassSearch,
    PassWindow,
    require_utc,
)
from meridian.registry.capability_match import (
    ReceiveCapability,
    covering_capabilities,
    lowest_elevation_floor_deg,
)
from meridian.store.element_sets import StoredElementSet, find_element_set_current_at
from meridian.store.passes import NewPass, insert_pass
from meridian.store.satellites import StoredTransmitter, find_active_transmitters
from meridian.store.station_capabilities import find_capabilities_for_station
from meridian.store.stations import (
    Connection,
    ReceivingStation,
    find_receiving_stations,
)

__all__ = [
    "GenerationHorizon",
    "GenerationReport",
    "generate_passes",
]


@dataclass(frozen=True, slots=True)
class GenerationHorizon:
    """The interval to generate passes over.

    Half-open on acquisition, matching ``OrbitService.pass_windows`` and
    ``store.passes.find_passes_in_horizon``: a pass belongs to the horizon it
    rises in (D-059). Two adjacent runs therefore partition the passes between
    them rather than both claiming one that straddles the seam.
    """

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """What one run did, in enough detail to explain an empty result.

    A job that generated nothing is the case worth reporting well: it can mean
    no station is registered, no station can receive anything tracked, no
    satellite has an element set old enough to be current, or simply that
    everything was already stored. Those are four different problems and a bare
    count of zero distinguishes none of them.
    """

    stations_considered: int
    pairs_propagated: int
    """Station-and-satellite pairs the geometry was actually computed for."""

    passes_computed: int
    passes_stored: int
    """Rows that landed. ``passes_computed - passes_stored`` were already held —
    on a re-run over an unchanged horizon that difference is the whole total."""

    satellites_without_element_set: tuple[str, ...]
    """Tracked satellites the archive could not place at the horizon start.

    Reported rather than skipped silently: the object is being tracked and has a
    live transmitter, so its absence from the output is a gap in the archive
    rather than a decision, and an operator can act on it.
    """


@dataclass(frozen=True, slots=True)
class _Receiver:
    """One station and the hardware it declared, ready to be matched."""

    station: ReceivingStation
    capabilities: tuple[ReceiveCapability, ...]


@dataclass(frozen=True, slots=True)
class _Catalogue:
    """What is tracked and predictable over one horizon.

    Loaded once for the whole run rather than per station: the element set
    current at the horizon start is a property of the satellite and the archive,
    identical for every station, and re-reading it inside the station loop would
    make the job's cost quadratic in the network for no change in its answer.
    """

    transmitters_by_satellite: dict[str, list[StoredTransmitter]]
    element_sets: dict[str, StoredElementSet]
    """Keyed by satellite id, holding only satellites the archive can place."""

    satellites_without_element_set: tuple[str, ...]


def _load_receivers(conn: Connection) -> list[_Receiver]:
    """Read every schedulable station together with its declared hardware."""
    receivers = []
    for station in find_receiving_stations(conn):
        stored = find_capabilities_for_station(conn, station.station_id)
        receivers.append(
            _Receiver(
                station=station,
                capabilities=tuple(
                    ReceiveCapability(
                        freq_min_hz=capability.freq_min_hz,
                        freq_max_hz=capability.freq_max_hz,
                        modes=tuple(capability.modes),
                        min_elevation_deg=capability.min_elevation_deg,
                    )
                    for capability in stored
                ),
            )
        )
    return receivers


def _load_catalogue(conn: Connection, at: datetime) -> _Catalogue:
    """Read the live downlinks, and the element set current at ``at`` for each.

    ``at`` is the horizon start rather than the wall clock, so re-running the
    job over a past horizon uses the element set that was current *then*. Using
    the newest set instead would silently change the answer every time a
    catalogue published, and the second run would store a duplicate of every
    pass rather than nothing.
    """
    transmitters_by_satellite: dict[str, list[StoredTransmitter]] = {}
    for transmitter in find_active_transmitters(conn):
        group = transmitters_by_satellite.setdefault(transmitter.satellite_id, [])
        group.append(transmitter)

    element_sets: dict[str, StoredElementSet] = {}
    missing: list[str] = []
    for satellite_id in transmitters_by_satellite:
        current = find_element_set_current_at(conn, satellite_id, at)
        if current is None:
            missing.append(satellite_id)
            continue
        element_sets[satellite_id] = current

    return _Catalogue(
        transmitters_by_satellite=transmitters_by_satellite,
        element_sets=element_sets,
        satellites_without_element_set=tuple(missing),
    )


def _store_passes(conn: Connection, computed: list[NewPass]) -> int:
    """Insert each pass, returning how many were not already held."""
    return sum(1 for one in computed if insert_pass(conn, one))


@dataclass(frozen=True, slots=True)
class _ReceiverTally:
    """One station's contribution to the run."""

    passes: list[NewPass]
    pairs_propagated: int


def _receivable_floors_deg(
    receiver: _Receiver, transmitters: list[StoredTransmitter]
) -> list[float]:
    """The elevation floor for each downlink of one satellite this station hears.

    One entry per receivable transmitter, so an empty list means the station
    cannot receive this satellite at all and the pair is skipped before anything
    is propagated. A satellite with two downlinks a station can take on
    different antennas contributes two floors, and the caller searches at the
    lower — the pass is receivable if any of the hardware can take it.
    """
    floors = []
    for transmitter in transmitters:
        covering = covering_capabilities(
            receiver.capabilities, transmitter.centre_freq_hz, transmitter.mode
        )
        if covering:
            floors.append(lowest_elevation_floor_deg(covering))
    return floors


def _to_element_set(stored: StoredElementSet) -> ElementSet:
    """The orbit module's shape of an archived element set."""
    return ElementSet(
        satellite_id=stored.satellite_id,
        epoch=stored.epoch,
        line1=stored.line1,
        line2=stored.line2,
        source=stored.source,
    )


def _to_new_pass(
    window: PassWindow, station: ReceivingStation, element_set_id: int
) -> NewPass:
    """One computed window in insertable form, for one station.

    ``simulated`` is copied from the station's registration record and from
    nowhere else. A pass predicted for a virtual station is simulated data at
    every layer (D-013, CLAUDE.md rule 5), and the flag has no other admissible
    source — there is no payload here to take it from and there never should be.
    """
    return NewPass(
        satellite_id=window.satellite_id,
        station_id=station.station_id,
        aos=window.aos,
        los=window.los,
        max_elevation_deg=window.max_elevation_deg,
        max_elevation_at=window.max_elevation_at,
        aos_azimuth_deg=window.aos_azimuth_deg,
        los_azimuth_deg=window.los_azimuth_deg,
        element_set_id=element_set_id,
        min_elevation_deg=window.min_elevation_deg,
        simulated=station.simulated,
    )


def _passes_for_receiver(
    orbit: OrbitService,
    receiver: _Receiver,
    catalogue: _Catalogue,
    horizon: GenerationHorizon,
) -> _ReceiverTally:
    """Every pass one station could take over the horizon.

    No database access: the catalogue was read once for the whole run and the
    propagator is pure computation, so this — the part that decides which
    station gets which pass — is testable on values alone.
    """
    computed: list[NewPass] = []
    pairs_propagated = 0

    for satellite_id, transmitters in catalogue.transmitters_by_satellite.items():
        stored_set = catalogue.element_sets.get(satellite_id)
        if stored_set is None:
            continue

        floors_deg = _receivable_floors_deg(receiver, transmitters)
        if not floors_deg:
            continue

        pairs_propagated += 1
        windows = orbit.pass_windows(
            PassSearch(
                element_set=_to_element_set(stored_set),
                site=GroundSite(
                    lat_deg=receiver.station.lat_deg,
                    lon_deg=receiver.station.lon_deg,
                    alt_m=receiver.station.alt_m,
                ),
                start=horizon.start,
                end=horizon.end,
                min_elevation_deg=min(floors_deg),
            )
        )
        computed.extend(
            _to_new_pass(window, receiver.station, stored_set.id) for window in windows
        )

    return _ReceiverTally(passes=computed, pairs_propagated=pairs_propagated)


def generate_passes(
    conn: Connection, orbit: OrbitService, horizon: GenerationHorizon
) -> GenerationReport:
    """Compute and store every pass every registered station could take.

    Args:
        conn: An open connection. Each pass is inserted in its own transaction,
            so a run interrupted halfway leaves the passes it had already
            computed rather than discarding them — they are correct predictions,
            and the next run recomputes the rest and re-stores none of these.
        orbit: The propagator, injected rather than constructed here so the
            decision logic can be exercised against a stub that returns known
            windows instead of against real orbital geometry.
        horizon: The interval to generate over, timezone-aware UTC.

    Returns:
        A :class:`GenerationReport` describing what happened, including the
        tracked satellites the archive could not place.

    Raises:
        ValueError: ``horizon.start`` or ``horizon.end`` is naive or not UTC.
            CLAUDE.local.md §6 makes that a bug rather than something to
            normalise, and this is the boundary where a caller's timestamp
            enters the platform.

    Note:
        **Re-running over an unchanged horizon stores nothing.** The element set
        is chosen as the one current at ``horizon.start``, the scan grid is
        anchored rather than relative to the horizon, and the identity in D-063
        covers exactly the fields that then repeat — so the second run computes
        the same acquisitions to the microsecond and every insert is a no-op.
        ``passes_computed`` still counts them, and the gap to ``passes_stored``
        is how a caller sees that nothing changed.

        A *newer* element set is a different prediction and does write new rows:
        the same physical passes, predicted better. That is deliberate (D-063),
        and it is why the completeness denominator groups by physical pass
        rather than counting rows.
    """
    require_utc(horizon.start, "horizon.start")
    require_utc(horizon.end, "horizon.end")

    receivers = _load_receivers(conn)
    catalogue = _load_catalogue(conn, horizon.start)

    computed: list[NewPass] = []
    pairs_propagated = 0
    for receiver in receivers:
        tally = _passes_for_receiver(orbit, receiver, catalogue, horizon)
        computed.extend(tally.passes)
        pairs_propagated += tally.pairs_propagated

    stored = _store_passes(conn, computed)

    return GenerationReport(
        stations_considered=len(receivers),
        pairs_propagated=pairs_propagated,
        passes_computed=len(computed),
        passes_stored=stored,
        satellites_without_element_set=catalogue.satellites_without_element_set,
    )

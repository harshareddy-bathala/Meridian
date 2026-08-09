"""Stations and their capabilities — the SQL layer registration is built on.

Reads and writes ``stations`` and ``station_capabilities``
(``deploy/migrations/sql/0002_stations.sql``). Like ``meridian.store.invites``,
this module makes no decision about *whether* a station should be created,
recovered or authenticated — it only persists and retrieves rows.
``meridian.registry.psycopg_registry`` decides; this module gives it something
typed to call.

Throughout, a soft-deleted station (``deleted_at`` set) reads as absent rather
than as a station in some other state, so a caller never has to ask.

The bearer-token columns are ``meridian.store.station_tokens``'. A station's
first token hash is written here, with the row that carries it; every rotation,
revocation and authentication lookup afterwards lives in that module, so the
credential lifecycle can be read end to end in one file.

Both secrets stored here — the bearer token and the registration key — arrive
already hashed. Hashing them needs ``Settings.token_hash_pepper`` (D-017,
D-023), which this module has no business reading.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import class_row

__all__ = [
    "Capability",
    "Connection",
    "NewStation",
    "StationHeartbeat",
    "StationProvenance",
    "StationRecoveryInfo",
    "find_station_for_recovery",
    "find_station_heartbeat",
    "find_station_provenance",
    "insert_station",
]

Connection = psycopg.Connection[tuple[object, ...]]
"""Matches :data:`meridian.store.invites.Connection` — a pooled connection or
a bare ``psycopg.connect()`` result, whichever the caller has."""

Cursor = psycopg.Cursor[tuple[object, ...]]
"""The cursor type the private row-writing helpers below take.

They take a cursor rather than a connection so that the transaction stays open
in the one public function that owns it, rather than each helper opening a
nested one of its own."""


@dataclass(frozen=True, slots=True)
class Capability:
    """One row of ``station_capabilities``, in insertable form.

    Mirrors the column list 1:1 rather than living in the registry service,
    the same way ``Invite`` lives in ``store.invites`` rather than in its
    caller. ``horizon_mask_json`` is pre-serialised JSON text — shaping and
    validating MSP §4.1's ``horizon_mask`` array is the API layer's Pydantic
    model, not this module's concern.
    """

    band: str
    freq_min_hz: int
    freq_max_hz: int
    modes: tuple[str, ...]
    polarisation: str
    tracking: bool
    min_elevation_deg: float
    horizon_mask_json: str = "[]"


@dataclass(frozen=True, slots=True)
class NewStation:
    """Everything :func:`insert_station` needs for one ``stations`` row.

    Bundled rather than passed as separate parameters: CLAUDE.local.md caps a
    function at four parameters (five, hard) and "take a dataclass" beyond
    that — precedent already set by ``OrbitService.pass_windows`` taking a
    ``PassSearch`` (D-044) for the same reason.
    """

    station_id: str
    name: str
    operator: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    token_sha256: bytes
    registration_key_sha256: bytes
    simulated: bool
    simulator_run_id: str | None
    seed: int | None
    client_implementation: str | None
    client_version: str | None


def insert_station(
    conn: Connection, station: NewStation, capabilities: Sequence[Capability]
) -> None:
    """Create one station and its capabilities, in one transaction.

    Args:
        conn: An open connection. This function manages its own transaction,
            which nests as a savepoint when the caller already has one open
            — see ``store.invites.create_invite`` for the same property,
            proven by ``tests/integration/test_store_invites.py``'s
            ``rollback`` fixture.
        station: The station row to insert.
        capabilities: One or more capability rows. MSP §4.1 requires at
            least one; that requirement is validated by the API layer, not
            enforced here — an empty sequence simply inserts zero rows.

    Raises:
        psycopg.errors.UniqueViolation: ``station.station_id`` already exists.
    """
    with conn.transaction(), conn.cursor() as cur:
        _insert_station_row(cur, station)
        _insert_capability_rows(cur, station.station_id, capabilities)


def _insert_station_row(cur: Cursor, station: NewStation) -> None:
    """Write the one ``stations`` row. Caller owns the transaction."""
    cur.execute(
        """
        insert into stations
            (station_id, name, operator, lat_deg, lon_deg, alt_m,
             token_sha256, registration_key_sha256, simulated,
             simulator_run_id, seed, client_implementation, client_version)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            station.station_id,
            station.name,
            station.operator,
            station.lat_deg,
            station.lon_deg,
            station.alt_m,
            station.token_sha256,
            station.registration_key_sha256,
            station.simulated,
            station.simulator_run_id,
            station.seed,
            station.client_implementation,
            station.client_version,
        ),
    )


def _insert_capability_rows(
    cur: Cursor, station_id: str, capabilities: Sequence[Capability]
) -> None:
    """Write one ``station_capabilities`` row per capability."""
    cur.executemany(
        """
        insert into station_capabilities
            (station_id, band, freq_min_hz, freq_max_hz, modes,
             polarisation, tracking, min_elevation_deg, horizon_mask_json)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        [
            (
                station_id,
                cap.band,
                cap.freq_min_hz,
                cap.freq_max_hz,
                list(cap.modes),
                cap.polarisation,
                cap.tracking,
                cap.min_elevation_deg,
                cap.horizon_mask_json,
            )
            for cap in capabilities
        ],
    )


@dataclass(frozen=True, slots=True)
class StationRecoveryInfo:
    """What MSP §4.1's recovery table needs to decide a registration retry.

    ``registration_key_sha256`` answers "does the presented key match".
    ``registered_at`` and ``last_heartbeat_at`` answer "is this station still
    inside D-023's recovery window" — a question that only applies to an
    *unbound* invite; a bound invite (D-034) ignores the window entirely, so
    the registry service reads these two fields only on that path.
    ``simulated`` answers "is the station recovering the same *kind* of
    station it registered as" (D-048).
    """

    registration_key_sha256: bytes
    registered_at: datetime
    last_heartbeat_at: datetime | None
    simulated: bool


def find_station_for_recovery(
    conn: Connection, station_id: str
) -> StationRecoveryInfo | None:
    """The fields needed to validate a registration retry against ``station_id``.

    Returns:
        ``None`` if there is no live station to recover. The foreign keys on
        ``invite_tokens.consumed_by_station_id`` and ``issued_for_station_id``
        guarantee the row exists, so in practice this means the station was
        deleted after its invite was written. The caller decides what that
        means rather than this function raising.

    Note:
        Deleted stations are excluded, matching
        ``station_tokens.find_station_id_by_token_hash`` and
        :func:`find_station_heartbeat`
        — throughout this module a deleted station reads as absent. Recovering
        one would mint a token that cannot then authenticate (D-058).
    """
    with conn.cursor(row_factory=class_row(StationRecoveryInfo)) as cur:
        cur.execute(
            """
            select registration_key_sha256, registered_at, last_heartbeat_at,
                   simulated
            from stations
            where station_id = %s
              and deleted_at is null
            """,
            (station_id,),
        )
        return cur.fetchone()


@dataclass(frozen=True, slots=True)
class StationProvenance:
    """Whether a station's data is simulated, and what produced it.

    The three fields ``0002_stations.sql`` keeps together under its
    ``station_simulated_fields`` CHECK: a simulated station carries a run
    id and a seed, a real one carries neither.
    """

    simulated: bool
    simulator_run_id: str | None
    seed: int | None


def find_station_provenance(
    conn: Connection, station_id: str
) -> StationProvenance | None:
    """The registry's answer to "is this station simulated", for one station.

    **This is the only admissible source of ``simulated`` for anything a
    station sends.** A heartbeat and an observation both carry a
    ``simulated`` field on the wire, and a station is free to put whatever it
    likes there; taking it at face value would let a simulated station file
    measured-looking rows, which is the failure CLAUDE.md's fifth rule exists
    to prevent. ``meridian.observations``' own module docstring states the
    same invariant. The value belongs to the registration record, and this is
    how a caller reads it back.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The station to look up, already authenticated.

    Returns:
        Its provenance, or ``None`` when no such station exists.
    """
    with conn.cursor(row_factory=class_row(StationProvenance)) as cur:
        cur.execute(
            """
            select simulated, simulator_run_id, seed
            from stations
            where station_id = %s
            """,
            (station_id,),
        )
        return cur.fetchone()


@dataclass(frozen=True, slots=True)
class StationHeartbeat:
    """When a station last reported, if it ever has."""

    last_heartbeat_at: datetime | None
    """Timezone-aware UTC, or ``None`` when the station has never heartbeat.

    ``None`` is not "very old". A station that registered and never reported is
    a commissioning problem; one that reported and stopped is a fault in
    something that was working. ``registry.liveness.derive_liveness`` keeps them
    apart as ``never_seen`` and ``offline``.
    """


def find_station_heartbeat(
    conn: Connection, station_id: str
) -> StationHeartbeat | None:
    """When one station last reported.

    Separate from :func:`find_station_for_recovery`, which reads the same column
    among four others for a different question. A caller asking about liveness
    should not have to fetch a registration key hash to get it.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The station to look up.

    Returns:
        Its last heartbeat instant, or ``None`` when there is no such station.
        The two ``None``s are at different levels and mean different things: no
        row at all versus a row that has never reported. Deleted stations are
        excluded, so a soft-deleted station reads as absent rather than as
        permanently offline — it is not a fault to investigate.
    """
    with conn.cursor(row_factory=class_row(StationHeartbeat)) as cur:
        cur.execute(
            """
            select last_heartbeat_at
            from stations
            where station_id = %s
              and deleted_at is null
            """,
            (station_id,),
        )
        return cur.fetchone()

"""Stations and their capabilities — the SQL layer registration is built on.

Reads and writes ``stations`` and ``station_capabilities``
(``deploy/migrations/sql/0002_stations.sql``). Like ``meridian.store.invites``,
this module makes no decision about *whether* a station should be created,
recovered or authenticated — it only persists and retrieves rows. Stage 4.3's
registry service decides; this module gives it something typed to call.

Both secrets stored here — the bearer token and the registration key — arrive
already hashed. Hashing them needs ``Settings.token_hash_pepper`` (D-017,
D-023), which this module has no business reading.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import class_row, scalar_row

__all__ = [
    "Capability",
    "Connection",
    "NewStation",
    "StationHeartbeat",
    "StationProvenance",
    "StationRecoveryInfo",
    "find_station_for_recovery",
    "find_station_heartbeat",
    "find_station_id_by_token_hash",
    "find_station_provenance",
    "insert_station",
    "revoke_station_token",
    "rotate_station_token",
]

Connection = psycopg.Connection[tuple[object, ...]]
"""Matches :data:`meridian.store.invites.Connection` — a pooled connection or
a bare ``psycopg.connect()`` result, whichever the caller has."""


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
        cur.executemany(
            """
            insert into station_capabilities
                (station_id, band, freq_min_hz, freq_max_hz, modes,
                 polarisation, tracking, min_elevation_deg, horizon_mask_json)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            [
                (
                    station.station_id,
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
        ``None`` if no such station exists — which should not happen for a
        ``station_id`` reached via ``invite_tokens.consumed_by_station_id`` or
        ``issued_for_station_id``, both foreign keys, but the caller decides
        what that means rather than this function raising.
    """
    with conn.cursor(row_factory=class_row(StationRecoveryInfo)) as cur:
        cur.execute(
            """
            select registration_key_sha256, registered_at, last_heartbeat_at,
                   simulated
            from stations
            where station_id = %s
            """,
            (station_id,),
        )
        return cur.fetchone()


@dataclass(frozen=True, slots=True)
class StationProvenance:
    """Whether a station's data is simulated, and what produced it.

    The three fields ``0002_stations.sql`` keeps together under its
    ``station_simulated_together`` CHECK: a simulated station carries a run
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


def rotate_station_token(
    conn: Connection, *, station_id: str, token_sha256: bytes
) -> None:
    """Mint a fresh bearer token onto an existing station.

    The "newly minted token" outcome in every recovery row of MSP §4.1's
    table. Clears ``token_revoked_at`` as well as setting the new hash — a
    station recovered through a bound invite after a `401` (D-034) is, by
    that recovery, no longer revoked.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update stations
            set token_sha256 = %s, token_issued_at = now(), token_revoked_at = null
            where station_id = %s
            """,
            (token_sha256, station_id),
        )


def revoke_station_token(conn: Connection, *, station_id: str) -> bool:
    """Withdraw a station's bearer token, so the next request it makes is a 401.

    The counterpart to :func:`rotate_station_token`, which clears this column.
    Nothing wrote ``token_revoked_at`` before this function existed — the column
    was declared in ``0002_stations.sql`` and read by
    :func:`find_station_id_by_token_hash`, and the only statement touching it
    *cleared* it, so a compromised station could not be shut out at all.

    Args:
        conn: Open connection. The caller owns the transaction.
        station_id: The station whose credential is being withdrawn.

    Returns:
        True when a live token was withdrawn. False when the station does not
        exist, is deleted, or was already revoked — the three cases an operator
        does not need told apart, since in all of them the station holds no
        working token afterwards. Matching ``revoke_invite``'s shape.

    Note:
        Guarded on ``token_revoked_at is null`` so that revoking twice keeps the
        **first** timestamp. That instant is when the credential actually died,
        and overwriting it with the moment somebody repeated the command would
        lose the only record of when the exposure ended.

        Revocation takes effect on the next request with no invalidation step,
        because ``find_station_id_by_token_hash`` filters on this column at
        lookup rather than consulting a cache. Stage 5's completion gate asks
        for revocation to be immediate; it is immediate by construction.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update stations
            set token_revoked_at = now()
            where station_id = %s
              and token_revoked_at is null
              and deleted_at is null
            """,
            (station_id,),
        )
        return cur.rowcount > 0


def find_station_id_by_token_hash(conn: Connection, token_sha256: bytes) -> str | None:
    """The lookup half of ``Registry.authenticate()``.

    Excludes a revoked or deleted station — MSP §6 defines ``unauthorized``
    as covering a revoked token, and a deleted station has no valid identity
    to authenticate as. Deciding what to do with a ``None`` result (which
    covers "no such token", "revoked" and "deleted" alike, indistinguishably
    — MSP §3 does not let a client learn which) belongs to the registry
    service, not this function.
    """
    with conn.cursor(row_factory=scalar_row) as cur:
        cur.execute(
            """
            select station_id
            from stations
            where token_sha256 = %s
              and token_revoked_at is null
              and deleted_at is null
            """,
            (token_sha256,),
        )
        return cur.fetchone()

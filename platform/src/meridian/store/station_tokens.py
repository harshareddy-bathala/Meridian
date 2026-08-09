"""Bearer tokens — minting one, withdrawing one, and the lookup that accepts one.

Reads and writes the credential columns of ``stations``
(``deploy/migrations/sql/0002_stations.sql``): the token hash, when it was
issued, and when it was revoked. ``meridian.store.stations`` owns the rest of
the row and writes the *first* token hash as part of creating a station; every
rotation, revocation and authentication afterwards happens here.

Tokens arrive already hashed. Hashing needs ``Settings.token_hash_pepper``
(D-017, D-023), which this module has no business reading — it stores and
matches bytes, and never sees a plaintext credential.

All three functions agree on which stations have a usable token: not revoked,
not soft-deleted. Because the accepting lookup applies that test at read time
rather than consulting a cache, a revocation takes effect on the station's very
next request with nothing to invalidate in between.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import scalar_row

__all__ = [
    "Connection",
    "find_station_id_by_token_hash",
    "revoke_station_token",
    "rotate_station_token",
]

Connection = psycopg.Connection[tuple[object, ...]]
"""Matches :data:`meridian.store.stations.Connection` — a pooled connection or
a bare ``psycopg.connect()`` result, whichever the caller has."""


def rotate_station_token(
    conn: Connection, *, station_id: str, token_sha256: bytes
) -> bool:
    """Mint a fresh bearer token onto an existing station.

    The "newly minted token" outcome in every recovery row of MSP §4.1's
    table. Clears ``token_revoked_at`` as well as setting the new hash — a
    station recovered through a bound invite after a `401` (D-034) is, by
    that recovery, no longer revoked.

    Args:
        conn: Open connection. This function manages its own transaction.
        station_id: The station to mint onto.
        token_sha256: The new bearer token hash, already peppered (D-017).

    Returns:
        True when a station took the new token. False when ``station_id``
        names no live station, and the caller is then holding a plaintext
        token that will never authenticate.

    Note:
        Deleted stations are excluded, so the token this mints and the token
        :func:`find_station_id_by_token_hash` will later accept are decided by
        the same rule. Without that clause a deleted station could complete a
        recovery, receive a `200` and a plaintext token, and then be refused
        on its very next request (D-058).
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update stations
            set token_sha256 = %s, token_issued_at = now(), token_revoked_at = null
            where station_id = %s
              and deleted_at is null
            """,
            (token_sha256, station_id),
        )
        return cur.rowcount > 0


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

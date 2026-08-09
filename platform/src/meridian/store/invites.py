"""Invite tokens — the SQL access layer behind ``meridian invite`` and registration.

Reads and writes ``invite_tokens`` (``deploy/migrations/sql/0002_stations.sql``).
This module makes no decision about whether an invite *should* exist or be
consumed; it only persists and retrieves rows. Issuing one is a CLI action
today (``meridian.cli``) and will also be an operator action from the
dashboard later. Consuming one is called from
``meridian.registry.psycopg_registry``, which decides whether MSP §4.1's
recovery table permits it.

Reference: docs/DECISIONS.md D-020 (invites are rows, not a config value) and
D-034 (bound invites for credential rotation).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import class_row

__all__ = [
    "Connection",
    "Invite",
    "consume_invite",
    "create_invite",
    "expiry_from_days",
    "find_invite_by_hash",
    "generate_invite_token",
    "hash_invite_token",
    "list_invites",
    "revoke_invite",
    "seed_bootstrap_invite",
]

Connection = psycopg.Connection[tuple[object, ...]]
"""Alias matching :mod:`meridian.store.pool`'s row type, so a caller passing
either a pooled connection or a bare ``psycopg.connect()`` result — as
``meridian.cli`` does — satisfies the same signature."""


@dataclass(frozen=True, slots=True)
class Invite:
    """One row of ``invite_tokens``, without the hash — nothing reads it back."""

    label: str
    created_at: datetime
    expires_at: datetime | None

    is_expired: bool
    """Whether ``expires_at`` has passed, **evaluated by the database** (D-046).

    Carried as a column rather than recomputed by callers because
    :func:`revoke_invite` writes ``expires_at`` with the database's clock, and
    comparing that against a Python ``datetime.now(UTC)`` compares two clocks.
    They disagree: measured on this project's own development machine,
    Postgres ``now()`` ran roughly 6 ms ahead of ``datetime.now(UTC)``, whose
    resolution is 15.6 ms on Windows anyway (D-045). A revocation is therefore
    invisible to a Python-side comparison for several milliseconds after it
    commits — small, but the margin at the instant of revocation is zero by
    construction, so any skew at all is enough.

    Note ``now()`` in Postgres is transaction start, not statement time. That
    is the correct reading here: every row a single query returns is then
    judged against one instant.
    """

    consumed_at: datetime | None
    consumed_by_station_id: str | None
    issued_for_station_id: str | None


def hash_invite_token(token: str) -> bytes:
    """Hash a plaintext invite token the way it is stored and looked up.

    Plain SHA-256 — unlike a station bearer token (D-017), an invite carries
    no pepper. ``TOKEN_HASH_PEPPER`` in ``deploy/.env.example`` is documented
    as scoped to station tokens: an invite is already single-use and
    short-lived, so a hash lifted from a database leak buys an attacker a
    token that expires or gets consumed, not a standing credential.

    A named function rather than an inline call at each of
    :func:`generate_invite_token` and the registry service's presented-token
    lookup, so "how an invite token is hashed" exists in exactly one place.
    """
    return hashlib.sha256(token.encode("ascii")).digest()


def generate_invite_token() -> tuple[str, bytes]:
    """A fresh invite: the plaintext to display once, and the hash to store.

    32 bytes from ``secrets.token_urlsafe``, hashed by :func:`hash_invite_token`.
    """
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_invite_token(plaintext)


def expiry_from_days(days: int | None, *, now: datetime) -> datetime | None:
    """Convert ``--expires-in-days`` into a timestamp, or ``None`` for no expiry."""
    if days is None:
        return None
    return now + timedelta(days=days)


def create_invite(
    conn: Connection,
    *,
    label: str,
    expires_at: datetime | None,
    issued_for_station_id: str | None,
) -> str:
    """Insert one invite and return its plaintext token.

    The plaintext exists only in this call's return value and whatever the
    caller does with it — MSP §3's "displayed once." The row keeps only the
    hash.

    Args:
        conn: An open connection. This function manages its own transaction.
        label: Who the invite is for, as the operator names them.
        expires_at: When the invite stops being presentable, or ``None``.
        issued_for_station_id: Set to bind the invite to an existing station
            (D-034's rotation path); ``None`` admits a new station.

    Returns:
        The plaintext token.

    Raises:
        psycopg.errors.ForeignKeyViolation: ``issued_for_station_id`` names no
            station.
    """
    plaintext, token_sha256 = generate_invite_token()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into invite_tokens
                (token_sha256, label, expires_at, issued_for_station_id)
            values (%s, %s, %s, %s)
            """,
            (token_sha256, label, expires_at, issued_for_station_id),
        )
    return plaintext


def list_invites(conn: Connection) -> list[Invite]:
    """Every invite, newest first. Never returns a hash or a plaintext token.

    Uses ``class_row`` rather than ``Invite(*row)`` so the column list and the
    dataclass fields are matched by name, not position — a reordered
    ``select`` and a reordered dataclass would otherwise both type-check and
    silently swap two columns.
    """
    with conn.cursor(row_factory=class_row(Invite)) as cur:
        cur.execute(
            """
            select label, created_at, expires_at,
                   (expires_at is not null and expires_at <= now()) as is_expired,
                   consumed_at, consumed_by_station_id, issued_for_station_id
            from invite_tokens
            order by created_at desc
            """
        )
        return cur.fetchall()


def find_invite_by_hash(conn: Connection, token_sha256: bytes) -> Invite | None:
    """The invite a presented ``invite_token`` hashes to, or ``None``.

    Reuses :class:`Invite` unchanged — its fields (``expires_at``,
    ``consumed_at``, ``consumed_by_station_id``, ``issued_for_station_id``)
    are exactly MSP §4.1's recovery-table inputs, and a station-facing
    lookup has no more business returning a hash than an operator-facing
    ``list_invites`` does.
    """
    with conn.cursor(row_factory=class_row(Invite)) as cur:
        cur.execute(
            """
            select label, created_at, expires_at,
                   (expires_at is not null and expires_at <= now()) as is_expired,
                   consumed_at, consumed_by_station_id, issued_for_station_id
            from invite_tokens
            where token_sha256 = %s
            """,
            (token_sha256,),
        )
        return cur.fetchone()


def consume_invite(conn: Connection, *, token_sha256: bytes, station_id: str) -> bool:
    """Mark one invite consumed by ``station_id``, unless something already did.

    ``where consumed_at is null`` is the whole race guard: two registration
    requests racing to consume the same invite cannot both succeed, because
    only the first ``update`` matches a row. The second sees ``rowcount ==
    0`` and must treat that as ``invalid_invite``, exactly as it would for an
    invite that was never valid — MSP §3 does not let a client distinguish
    the two.

    Returns:
        Whether this call was the one that consumed it.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update invite_tokens
            set consumed_at = now(), consumed_by_station_id = %s
            where token_sha256 = %s
              and consumed_at is null
            """,
            (station_id, token_sha256),
        )
        return cur.rowcount > 0


def revoke_invite(conn: Connection, *, label: str) -> int:
    """Expire every unconsumed, not-yet-expired invite under ``label``.

    Revocation reuses ``expires_at`` rather than a ``revoked_at`` column: an
    invite that has been withdrawn and one that has expired are the same fact
    to every future reader of this table — "cannot be presented" — and
    ``deploy/migrations`` may not be edited once merged (Rule 9), so a second
    column would be a migration this table does not need.

    If more than one revocable invite shares ``label`` — there is no
    uniqueness constraint on it — all of them are revoked. "Withdraw
    everything issued under this label" is the more useful default for an
    operator who reissued under the same name.

    Returns:
        How many invites were revoked. Zero means the label matched nothing
        revocable — already consumed, already expired, or never issued.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update invite_tokens
            set expires_at = now()
            where label = %s
              and consumed_at is null
              and (expires_at is null or expires_at > now())
            """,
            (label,),
        )
        return cur.rowcount


def seed_bootstrap_invite(
    conn: Connection,
    *,
    token: str,
    label: str = "environment bootstrap",
) -> bool:
    """Seed one invite from ``REGISTRATION_INVITE_TOKEN`` on an empty database.

    D-020: a fresh ``docker compose up`` must be immediately registerable
    without a manual ``meridian invite create`` first. Safe to call
    unconditionally on every startup — the ``where not exists`` makes the
    insert a no-op the moment any row, consumed or not, already exists.

    Returns:
        Whether it inserted. ``False`` means the table already held a row and
        nothing was touched, per D-020's "without overwriting or recreating
        consumed invites."
    """
    token_sha256 = hash_invite_token(token)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into invite_tokens (token_sha256, label)
            select %s, %s
            where not exists (select 1 from invite_tokens)
            """,
            (token_sha256, label),
        )
        return cur.rowcount > 0

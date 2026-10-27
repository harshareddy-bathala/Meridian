"""Receive-chain warnings — raised once, cleared once, never deleted.

Reads and writes ``receive_chain_warnings``
(``deploy/migrations/sql/0030_receive_chain_watch.sql``).

**What this module allows is the rule** (D-292). One insert per warning, at
most one open per chain, method and configuration. One update, writing
``cleared_at`` and the shortfall at that moment together, only while the
warning is open. Nothing here deletes a warning: SC-9 counts false alarms from
these rows.

**A warning takes its station, chain and ``simulated`` from its baseline**,
copied by the insert, and the table's key refuses any other combination. So a
warning on a measured station can only name that station's own measured
baseline (D-291).

Reference: docs/DECISIONS.md D-104, D-291, D-292.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "NewWarning",
    "StoredWarning",
    "clear_warning",
    "find_open_warnings",
    "find_warnings",
    "has_open_warning",
    "raise_warning",
]


@dataclass(frozen=True, slots=True)
class NewWarning:
    """One warning, as raised."""

    baseline_id: int
    raised_at: datetime
    shortfall_db: float
    affected_bins: list[dict[str, object]]
    evidence: dict[str, object]
    method: str
    config_sha256: bytes


@dataclass(frozen=True, slots=True)
class _Id:
    id: int


def raise_warning(conn: Connection, row: NewWarning) -> int | None:
    """Raise a warning, unless one is already open for the chain.

    Returns:
        The new warning's id, or ``None`` when one was already open under this
        method and configuration, which says the same thing.

    Raises:
        LookupError: The baseline does not exist.
    """
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute("select id from signal_baselines where id = %s", (row.baseline_id,))
        if cur.fetchone() is None:
            raise LookupError(f"no baseline {row.baseline_id}")
        cur.execute(
            """
            insert into receive_chain_warnings (
                station_id, capability_id, baseline_id, raised_at, shortfall_db,
                affected_bins_json, evidence_json, method, config_sha256,
                simulated)
            select b.station_id, b.capability_id, b.id, %s, %s, %s, %s, %s, %s,
                   b.simulated
            from signal_baselines b where b.id = %s
            on conflict (station_id, capability_id, method, config_sha256)
                where cleared_at is null
                do nothing
            returning id
            """,
            (
                row.raised_at,
                row.shortfall_db,
                Jsonb(row.affected_bins),
                Jsonb(row.evidence),
                row.method,
                row.config_sha256,
                row.baseline_id,
            ),
        )
        written = cur.fetchone()
    return None if written is None else written.id


def clear_warning(
    conn: Connection, warning_id: int, *, at: datetime, shortfall_db: float
) -> bool:
    """Clear an open warning, recording the shortfall it recovered to.

    Returns:
        Whether it was cleared now; ``False`` if it was already clear.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            update receive_chain_warnings
            set cleared_at = %s, cleared_shortfall_db = %s
            where id = %s and cleared_at is null
            """,
            (at, shortfall_db, warning_id),
        )
        return cur.rowcount > 0


@dataclass(frozen=True, slots=True)
class StoredWarning:
    """One stored warning."""

    id: int
    station_id: str
    capability_id: int
    baseline_id: int
    raised_at: datetime
    shortfall_db: float
    affected_bins_json: list[dict[str, object]]
    evidence_json: dict[str, object]
    cleared_at: datetime | None
    cleared_shortfall_db: float | None
    method: str
    config_sha256: bytes
    simulated: bool


_COLUMNS = """
    id, station_id, capability_id, baseline_id, raised_at, shortfall_db,
    affected_bins_json, evidence_json, cleared_at, cleared_shortfall_db,
    method, config_sha256, simulated
"""


def find_open_warnings(
    conn: Connection,
    *,
    method: str,
    config_sha256: bytes,
    station_id: str | None = None,
) -> list[StoredWarning]:
    """Every warning open under this method and configuration, oldest first."""
    with conn.cursor(row_factory=class_row(StoredWarning)) as cur:
        cur.execute(
            f"select {_COLUMNS} from receive_chain_warnings"
            " where cleared_at is null and method = %s and config_sha256 = %s"
            " and (%s::text is null or station_id = %s)"
            " order by raised_at, id",
            (method, config_sha256, station_id, station_id),
        )
        return cur.fetchall()


def find_warnings(conn: Connection, station_id: str) -> list[StoredWarning]:
    """Every warning a station has had, open or cleared, oldest first."""
    with conn.cursor(row_factory=class_row(StoredWarning)) as cur:
        cur.execute(
            f"select {_COLUMNS} from receive_chain_warnings"
            " where station_id = %s order by raised_at, id",
            (station_id,),
        )
        return cur.fetchall()


def has_open_warning(conn: Connection, *, station_id: str, capability_id: int) -> bool:
    """Whether the chain has a warning open under any method or configuration.

    A baseline is not trained again while one is, so a shortfall the watch has
    already seen is not learned as the chain's normal signal (D-293).
    """
    with conn.cursor() as cur:
        cur.execute(
            "select exists (select 1 from receive_chain_warnings"
            " where station_id = %s and capability_id = %s and cleared_at is null)",
            (station_id, capability_id),
        )
        found = cur.fetchone()
    return bool(found and found[0])

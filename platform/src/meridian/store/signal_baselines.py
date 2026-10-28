"""Signal baselines — one receive chain's signal by elevation, written once.

Reads and writes ``signal_baselines``
(``deploy/migrations/sql/0030_receive_chain_watch.sql``).

**A row is a whole baseline** (D-291): every bin trained together over one
interval, from one kind of evidence, so a warning names exactly the bins it was
judged against.

**Written once.** An insert that meets the same chain, evidence, method,
configuration and interval writes nothing and returns the row already there. A
baseline is never updated; a later interval is a new row beside it.

**``simulated`` is the station's**, copied by the insert from its registry
record and never taken from the caller (D-013).

Reference: docs/DECISIONS.md D-013, D-104, D-290, D-291.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "NewBaseline",
    "StoredBaseline",
    "find_current_baseline",
    "insert_baseline",
]


@dataclass(frozen=True, slots=True)
class NewBaseline:
    """One baseline, as written."""

    station_id: str
    capability_id: int
    evidence: str
    """``samples`` (MSP 0.3's placed samples) or ``peak`` (MSP 0.2's peak)."""

    bin_width_deg: float
    bins: list[dict[str, object]]
    receptions: int
    trained_from: datetime
    trained_until: datetime
    method: str
    config_sha256: bytes


@dataclass(frozen=True, slots=True)
class _Id:
    id: int


def insert_baseline(conn: Connection, row: NewBaseline) -> int:
    """Write one baseline, or find the one this interval already wrote.

    Returns:
        The baseline's id.

    Raises:
        LookupError: The station does not exist.
    """
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute(
            """
            insert into signal_baselines (
                station_id, capability_id, evidence, bin_width_deg, bins_json,
                receptions, trained_from, trained_until, method, config_sha256,
                simulated)
            select %(station)s, %(capability)s, %(evidence)s, %(width)s,
                   %(bins)s, %(receptions)s, %(since)s, %(until)s, %(method)s,
                   %(config)s, s.simulated
            from stations s where s.station_id = %(station)s
            on conflict on constraint signal_baseline_once do nothing
            returning id
            """,
            _parameters(row),
        )
        written = cur.fetchone()
        if written is not None:
            return written.id
        cur.execute(
            """
            select id from signal_baselines
            where station_id = %(station)s and capability_id = %(capability)s
              and evidence = %(evidence)s and method = %(method)s
              and config_sha256 = %(config)s
              and trained_from = %(since)s and trained_until = %(until)s
            """,
            _parameters(row),
        )
        found = cur.fetchone()
    if found is None:
        raise LookupError(f"no station {row.station_id}")
    return found.id


def _parameters(row: NewBaseline) -> dict[str, object]:
    return {
        "station": row.station_id,
        "capability": row.capability_id,
        "evidence": row.evidence,
        "width": row.bin_width_deg,
        "bins": Jsonb(row.bins),
        "receptions": row.receptions,
        "since": row.trained_from,
        "until": row.trained_until,
        "method": row.method,
        "config": row.config_sha256,
    }


@dataclass(frozen=True, slots=True)
class StoredBaseline:
    """One stored baseline."""

    id: int
    station_id: str
    capability_id: int
    evidence: str
    bin_width_deg: float
    bins_json: list[dict[str, object]]
    receptions: int
    trained_from: datetime
    trained_until: datetime
    method: str
    config_sha256: bytes
    computed_at: datetime
    simulated: bool


def find_current_baseline(  # noqa: PLR0913 — one chain's key, each by name
    conn: Connection,
    *,
    station_id: str,
    capability_id: int,
    evidence: str,
    method: str,
    config_sha256: bytes,
) -> StoredBaseline | None:
    """The chain's latest baseline under this method and configuration.

    Latest by the end of its training interval, so a baseline built later over
    an earlier interval never displaces one that saw more recent signal.
    """
    with conn.cursor(row_factory=class_row(StoredBaseline)) as cur:
        cur.execute(
            """
            select id, station_id, capability_id, evidence, bin_width_deg,
                   bins_json, receptions, trained_from, trained_until, method,
                   config_sha256, computed_at, simulated
            from signal_baselines
            where station_id = %s and capability_id = %s and evidence = %s
              and method = %s and config_sha256 = %s
            order by trained_until desc, id desc
            limit 1
            """,
            (station_id, capability_id, evidence, method, config_sha256),
        )
        return cur.fetchone()

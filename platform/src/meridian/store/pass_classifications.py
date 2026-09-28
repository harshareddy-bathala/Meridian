"""The ``pass_classifications`` table: what happened to each settled pass.

Written by ``meridian.reliability.accounting`` and read by every reliability
figure. Append-only: a row is never updated, and re-running under the same
method and configuration writes nothing, because the table's unique key says
so rather than a check made here (D-182).

Reference: docs/DECISIONS.md D-180, D-182; migration 0017.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "NewClassification",
    "StoredClassification",
    "find_classifications_of",
    "insert_classification",
]


@dataclass(frozen=True, slots=True)
class NewClassification:
    """One physical pass's classification, and the evidence it was decided from."""

    assignment_ids: Sequence[str]
    """Every scheduled assignment pooled into the pass, sorted; the first is its
    representative and the row's key."""

    pass_id: int
    station_id: str
    satellite_id: str
    window_start: datetime
    window_end: datetime
    classification: str
    evidence: Mapping[str, object]
    method: str
    config_sha256: bytes
    simulated: bool


def insert_classification(conn: Connection, row: NewClassification) -> bool:
    """Store one classification, unless this method and configuration hold it.

    Args:
        conn: An open connection. This function owns its transaction.
        row: The classification.

    Returns:
        True if a row was written; False if one was already held for the
        representative assignment under this method and configuration.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into pass_classifications (
                assignment_id, assignment_ids, pass_id, station_id, satellite_id,
                window_start, window_end, classification, evidence, method,
                config_sha256, simulated
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict on constraint pass_classification_once do nothing
            """,
            (
                row.assignment_ids[0],
                list(row.assignment_ids),
                row.pass_id,
                row.station_id,
                row.satellite_id,
                row.window_start,
                row.window_end,
                row.classification,
                Jsonb(dict(row.evidence)),
                row.method,
                row.config_sha256,
                row.simulated,
            ),
        )
        return cur.rowcount > 0


@dataclass(frozen=True, slots=True)
class StoredClassification:
    """One stored classification, whole, as ``reliability explain`` prints it."""

    classification_id: int
    assignment_ids: list[str]
    pass_id: int
    station_id: str
    satellite_id: str
    window_start: datetime
    window_end: datetime
    classification: str
    evidence: dict[str, object]
    method: str
    config_sha256: bytes
    classified_at: datetime
    simulated: bool


def find_classifications_of(
    conn: Connection, assignment_id: str
) -> list[StoredClassification]:
    """Every classification a scheduled assignment was pooled into.

    Args:
        conn: An open connection. Read-only.
        assignment_id: Any assignment of the pass, not only its representative.

    Returns:
        One row per method and configuration it was classified under, oldest
        first.
    """
    with conn.cursor(row_factory=class_row(StoredClassification)) as cur:
        cur.execute(
            """
            select classification_id, assignment_ids, pass_id, station_id,
                   satellite_id, window_start, window_end, classification,
                   evidence, method, config_sha256, classified_at, simulated
            from pass_classifications
            where %s = any(assignment_ids)
            order by classified_at, classification_id
            """,
            (assignment_id,),
        )
        return cur.fetchall()

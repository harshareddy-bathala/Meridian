"""What the network's weekly report counts, beyond the classifications.

Reads ``stations``, ``heartbeats``, ``pass_classifications``,
``loss_diagnoses`` and ``receive_chain_warnings``. Writes nothing.

Every read counts, for measured and simulated stations apart (rule 5), and
names nothing: no station, pass or place leaves this module, only how many.
A soft-deleted station is not counted, as it is absent from every public read.

Reference: docs/DECISIONS.md D-182, D-272, D-367.
"""

from __future__ import annotations

from datetime import datetime

from meridian.store.stations import Connection

__all__ = [
    "count_causes_between",
    "count_stations",
    "count_stations_heard",
    "count_warnings_open",
    "first_registered_at",
]

_Counts = dict[bool, int]
"""A count per population, keyed by ``simulated``. A missing key is zero."""


def _by_population(rows: list[tuple[object, ...]]) -> _Counts:
    return {bool(row[0]): int(str(row[1])) for row in rows}


def count_stations(conn: Connection, at: datetime) -> _Counts:
    """Stations registered before ``at`` and not removed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select simulated, count(*) from stations
            where registered_at < %s and deleted_at is null
            group by simulated
            """,
            (at,),
        )
        return _by_population(cur.fetchall())


def count_stations_heard(conn: Connection, start: datetime, end: datetime) -> _Counts:
    """Stations not removed whose heartbeat arrived in ``[start, end)``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select s.simulated, count(*) from stations s
            where s.deleted_at is null and exists (
                select 1 from heartbeats h
                where h.station_id = s.station_id
                  and h.received_at >= %s and h.received_at < %s
            )
            group by s.simulated
            """,
            (start, end),
        )
        return _by_population(cur.fetchall())


def count_warnings_open(conn: Connection, at: datetime) -> _Counts:
    """Receive-chain warnings raised before ``at`` and not cleared by it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select s.simulated, count(*)
            from receive_chain_warnings w
            join stations s on s.station_id = w.station_id
            where s.deleted_at is null and w.raised_at < %s
              and (w.cleared_at is null or w.cleared_at >= %s)
            group by s.simulated
            """,
            (at, at),
        )
        return _by_population(cur.fetchall())


def count_causes_between(
    conn: Connection,
    *,
    classified_under: tuple[str, bytes],
    diagnosed_under: tuple[str, bytes],
    window: tuple[datetime, datetime],
) -> list[tuple[bool, str, int]]:
    """Diagnosed causes of the passes whose window closed in ``window``.

    Each classified pass counts once, by its latest diagnosis under the
    deployment's method and configuration, as ``/results`` reads it (D-365).

    Returns:
        ``(simulated, cause, count)``, most frequent first within a population,
        then by cause.
    """
    classification_method, classification_sha = classified_under
    diagnosis_method, diagnosis_sha = diagnosed_under
    start, end = window
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.simulated, d.cause, count(*) as n
            from pass_classifications c
            join stations s on s.station_id = c.station_id
            join lateral (
                select one.cause
                from loss_diagnoses one
                where one.assignment_id = c.assignment_id
                  and one.classification_id = c.classification_id
                  and one.method = %(diagnosis_method)s
                  and one.config_sha256 = %(diagnosis_sha)s
                order by coalesce(one.revision, 0) desc, one.diagnosis_id desc
                limit 1
            ) d on true
            where s.deleted_at is null
              and c.method = %(classification_method)s
              and c.config_sha256 = %(classification_sha)s
              and c.window_end >= %(start)s and c.window_end < %(end)s
            group by c.simulated, d.cause
            order by c.simulated, n desc, d.cause
            """,
            {
                "classification_method": classification_method,
                "classification_sha": classification_sha,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha": diagnosis_sha,
                "start": start,
                "end": end,
            },
        )
        return [(bool(row[0]), str(row[1]), int(str(row[2]))) for row in cur.fetchall()]


def first_registered_at(conn: Connection) -> datetime | None:
    """When the earliest station still registered was, or ``None`` with none."""
    with conn.cursor() as cur:
        cur.execute("select min(registered_at) from stations where deleted_at is null")
        row = cur.fetchone()
    found = None if row is None else row[0]
    return found if isinstance(found, datetime) else None

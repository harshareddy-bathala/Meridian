"""What a weekly report reads: a station's week, by the rows its report names.

Reads ``stations``, ``assignments``, ``observations``, ``reception_verdicts``,
``loss_diagnoses``, ``pass_classifications``, ``heartbeats``,
``receive_chain_warnings``, ``station_capabilities`` and ``report_deliveries``.
Writes nothing.

A week is Monday 00:00 UTC to the next. Its passes are those the station held
whose window began in it, each named by the same key a pass report uses
(:class:`~meridian.store.report_reads.ReceptionKey`); its warnings are those
open at its end. Hours heard are counted from heartbeats, which a week that has
ended no longer gains (D-302).

Reference: docs/DECISIONS.md D-178, D-302, D-306.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.report_reads import (
    HELD_AND_CLASSIFIED,
    LATEST_ROWS,
    ReceptionKey,
    require,
)
from meridian.store.stations import Connection

__all__ = [
    "WarningRow",
    "WeekReception",
    "WeekStation",
    "find_queued_weeks",
    "find_week_receptions",
    "find_week_warnings",
    "hours_heard",
    "read_week_receptions",
    "read_week_station",
    "read_week_warnings",
]


def find_queued_weeks(
    conn: Connection, channels: dict[str, str], period_start: datetime
) -> set[str]:
    """The stations whose week from ``period_start`` is queued on their channel."""
    if not channels:
        return set()
    stations, chosen = zip(*sorted(channels.items()), strict=True)
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.station_id
            from unnest(%s::text[], %s::text[]) as r (station_id, channel)
            join report_deliveries q
              on q.station_id = r.station_id and q.channel = r.channel
             and q.kind = 'weekly' and q.period_start = %s
            """,
            (list(stations), list(chosen), period_start),
        )
        return {str(row[0]) for row in cur.fetchall()}


def find_week_receptions(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    station_id: str,
    start: datetime,
    end: datetime,
    *,
    classification_method: str,
    classification_sha256: bytes,
    diagnosis_method: str,
    diagnosis_sha256: bytes,
    verdict_method: str | None,
) -> list[ReceptionKey]:
    """The keys of every pass the station held whose window began in the week."""
    with conn.cursor(row_factory=class_row(ReceptionKey)) as cur:
        cur.execute(
            f"""
            select a.assignment_id, o.revision, v.method as verdict_method,
                   d.diagnosis_id
            from assignments a
            {LATEST_ROWS}
            where a.station_id = %(station)s
              and a.start_at >= %(start)s and a.start_at < %(end)s
              and {HELD_AND_CLASSIFIED}
            order by a.start_at, a.assignment_id
            """,
            {
                "station": station_id,
                "start": start,
                "end": end,
                "classification_method": classification_method,
                "classification_sha256": classification_sha256,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha256": diagnosis_sha256,
                "verdict_method": verdict_method,
            },
        )
        return cur.fetchall()


def find_week_warnings(conn: Connection, station_id: str, end: datetime) -> list[int]:
    """The ids of the station's receive-chain warnings open at ``end``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id from receive_chain_warnings
            where station_id = %s and raised_at < %s
              and (cleared_at is null or cleared_at >= %s)
            order by id
            """,
            (station_id, end, end),
        )
        return [int(str(row[0])) for row in cur.fetchall()]


@dataclass(frozen=True, slots=True)
class WeekStation:
    """The station a weekly report is about."""

    station_id: str
    name: str
    simulated: bool


def read_week_station(conn: Connection, station_id: str) -> WeekStation:
    """The station, by id.

    Raises:
        LookupError: There is no such station.
    """
    with conn.cursor(row_factory=class_row(WeekStation)) as cur:
        cur.execute(
            "select station_id, name, simulated from stations where station_id = %s",
            (station_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"a report names station {station_id}, which is not stored")
    return row


def hours_heard(
    conn: Connection, station_id: str, start: datetime, end: datetime
) -> int:
    """Hours in ``[start, end)`` in which at least one heartbeat arrived."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(distinct date_trunc('hour', received_at))
            from heartbeats
            where station_id = %s and received_at >= %s and received_at < %s
            """,
            (station_id, start, end),
        )
        found = cur.fetchone()
    return 0 if found is None else int(str(found[0]))


@dataclass(frozen=True, slots=True)
class WeekReception:
    """One pass of a week, read by the keys its report names."""

    assignment_id: str
    outcome: str | None
    probability_usable: float | None
    cause: str | None


def read_week_receptions(
    conn: Connection, keys: list[ReceptionKey]
) -> list[WeekReception]:
    """Exactly the rows ``keys`` name, in their order.

    Raises:
        LookupError: A named row is not there.
    """
    if not keys:
        return []
    with conn.cursor(row_factory=class_row(WeekReception)) as cur:
        cur.execute(
            """
            select k.assignment_id, o.outcome, v.probability_usable, d.cause
            from unnest(%s::text[], %s::integer[], %s::text[], %s::bigint[])
                 with ordinality as k (assignment_id, revision, verdict_method,
                                       diagnosis_id, n)
            left join observations o
                   on o.assignment_id = k.assignment_id and o.revision = k.revision
            left join reception_verdicts v
                   on v.assignment_id = k.assignment_id
                  and v.revision = k.revision and v.method = k.verdict_method
            left join loss_diagnoses d
                   on d.diagnosis_id = k.diagnosis_id
                  and d.assignment_id = k.assignment_id
            order by k.n
            """,
            (
                [one.assignment_id for one in keys],
                [one.revision for one in keys],
                [one.verdict_method for one in keys],
                [one.diagnosis_id for one in keys],
            ),
        )
        rows = cur.fetchall()
    for key, row in zip(keys, rows, strict=True):
        require(key.revision is None or row.outcome is not None, f"{key} observation")
        require(
            key.verdict_method is None or row.probability_usable is not None,
            f"{key} verdict",
        )
        require(key.diagnosis_id is None or row.cause is not None, f"{key} diagnosis")
    return rows


@dataclass(frozen=True, slots=True)
class WarningRow:
    """A receive-chain warning, with the chain it is about."""

    id: int
    band: str
    freq_min_hz: int
    freq_max_hz: int
    raised_at: datetime
    shortfall_db: float


def read_week_warnings(conn: Connection, ids: list[int]) -> list[WarningRow]:
    """Exactly the warnings ``ids`` name, in their order.

    Raises:
        LookupError: A named warning is not there.
    """
    if not ids:
        return []
    with conn.cursor(row_factory=class_row(WarningRow)) as cur:
        cur.execute(
            """
            select w.id, c.band, c.freq_min_hz, c.freq_max_hz, w.raised_at,
                   w.shortfall_db
            from unnest(%s::bigint[]) with ordinality as k (id, n)
            join receive_chain_warnings w on w.id = k.id
            join station_capabilities c on c.id = w.capability_id
            order by k.n
            """,
            (ids,),
        )
        rows = cur.fetchall()
    require(len(rows) == len(ids), f"warnings {ids}")
    return rows

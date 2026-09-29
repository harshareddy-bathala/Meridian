"""The rows a live reliability report is counted from.

Four reads, each deciding nothing: which passes were classified inside a
window, how many of each class there were, how many seconds each station's
heartbeats vouch for, and how long each report took to arrive. The
indicators are ``meridian.reliability``'s.

Reference: docs/DECISIONS.md D-182, D-184.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "ClassifiedRow",
    "StationOnline",
    "SubmissionDelay",
    "count_classified_between",
    "find_classified_between",
    "find_station_online_seconds",
    "find_submission_delays",
]


@dataclass(frozen=True, slots=True)
class ClassifiedRow:
    """One stored classification, reduced to what the indicators read."""

    classification_id: int
    assignment_id: str
    station_id: str
    window_end: datetime
    classification: str
    listening_confirmed: bool
    outcome: str | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class _ClassCount:
    classification: str
    simulated: bool
    n: int


@dataclass(frozen=True, slots=True)
class StationOnline:
    """Seconds a station's heartbeats vouch for, out of the seconds measured."""

    station_id: str
    simulated: bool
    covered_s: float
    span_s: float


@dataclass(frozen=True, slots=True)
class SubmissionDelay:
    """How long after its window closed a pass's report first arrived."""

    simulated: bool
    delay_s: float


def find_classified_between(
    conn: Connection,
    *,
    classified_under: tuple[str, bytes],
    window: tuple[datetime, datetime],
) -> list[ClassifiedRow]:
    """Classifications whose pass window closed inside ``[start, end)``.

    Args:
        conn: An open connection. Read-only.
        classified_under: The method and the configuration hash to read.
        window: The half-open interval of window ends.

    Returns:
        Each classification, in window order.
    """
    method, config_sha256 = classified_under
    start, end = window
    with conn.cursor(row_factory=class_row(ClassifiedRow)) as cur:
        cur.execute(
            """
            select classification_id, assignment_id, station_id, window_end,
                   classification,
                   coalesce((evidence ->> 'listening_confirmed')::boolean, false)
                       as listening_confirmed,
                   evidence -> 'report' ->> 'outcome' as outcome,
                   simulated
            from pass_classifications
            where method = %s and config_sha256 = %s
              and window_end >= %s and window_end < %s
            order by window_end, assignment_id
            """,
            (method, config_sha256, start, end),
        )
        return cur.fetchall()


def count_classified_between(
    conn: Connection,
    *,
    classified_under: tuple[str, bytes],
    window: tuple[datetime, datetime],
) -> dict[tuple[str, bool], int]:
    """How many classifications of each class and population closed in a window.

    What :func:`find_classified_between` returns, counted in the database, for a
    reader that needs the counts only: a metrics scrape reads this on every
    scrape, and must not carry a month of rows to count them.

    Args:
        conn: An open connection. Read-only.
        classified_under: The method and the configuration hash to read.
        window: The half-open interval of window ends.

    Returns:
        ``{(classification, simulated): count}`` for every pair with a row.
    """
    method, config_sha256 = classified_under
    start, end = window
    with conn.cursor(row_factory=class_row(_ClassCount)) as cur:
        cur.execute(
            """
            select classification, simulated, count(*)::int as n
            from pass_classifications
            where method = %s and config_sha256 = %s
              and window_end >= %s and window_end < %s
            group by classification, simulated
            """,
            (method, config_sha256, start, end),
        )
        return {(one.classification, one.simulated): one.n for one in cur.fetchall()}


def find_station_online_seconds(
    conn: Connection, *, window: tuple[datetime, datetime], offline_after_s: int
) -> list[StationOnline]:
    """Each registered station's heartbeat-covered seconds inside a window.

    A heartbeat vouches for the station from its arrival until the next one, or
    for ``offline_after_s`` if that comes first: past it, the registry calls
    the station offline (``OFFLINE_AFTER_S``, SC-5). A station is measured from
    the later of the window's start and its registration, so a station that
    joined yesterday is not charged for last week.

    Note:
        ``least`` and ``greatest`` ignore nulls in PostgreSQL, so the row the
        left join makes for a station with no heartbeat at all would count its
        whole span as covered. The ``filter`` is what keeps it at zero.

    Args:
        conn: An open connection. Read-only.
        window: ``[start, end)``.
        offline_after_s: The registry's offline threshold.

    Returns:
        One row per station not deleted and registered before the window's end,
        in station order.
    """
    start, end = window
    with conn.cursor(row_factory=class_row(StationOnline)) as cur:
        cur.execute(
            """
            with span as (
                select station_id, simulated,
                       greatest(registered_at, %(start)s) as span_start
                from stations
                where deleted_at is null and registered_at < %(end)s
            ),
            beats as (
                select station_id, received_at as at,
                       received_at + make_interval(secs => %(offline)s) as lapses_at,
                       lead(received_at) over (
                           partition by station_id order by received_at
                       ) as next_at
                from heartbeats
                where received_at >= %(start)s - make_interval(secs => %(offline)s)
                  and received_at < %(end)s
            )
            select s.station_id, s.simulated,
                   coalesce(sum(greatest(0, extract(epoch from
                       least(coalesce(b.next_at, b.lapses_at), b.lapses_at, %(end)s)
                       - greatest(b.at, s.span_start)
                   ))) filter (where b.at is not null), 0)::float8 as covered_s,
                   extract(epoch from (%(end)s - s.span_start))::float8 as span_s
            from span s
            left join beats b on b.station_id = s.station_id
            group by s.station_id, s.simulated, s.span_start
            order by s.station_id
            """,
            {"start": start, "end": end, "offline": offline_after_s},
        )
        return cur.fetchall()


def find_submission_delays(
    conn: Connection,
    *,
    classified_under: tuple[str, bytes],
    window: tuple[datetime, datetime],
) -> list[SubmissionDelay]:
    """How long each classified pass's report took to first arrive.

    Measured from the reporting assignment's window end to the arrival of its
    first revision, on the platform's clock (``submitted_at``). A pass with no
    report has no delay and is not counted here.
    """
    method, config_sha256 = classified_under
    start, end = window
    with conn.cursor(row_factory=class_row(SubmissionDelay)) as cur:
        cur.execute(
            """
            select c.simulated,
                   extract(epoch from (min(o.submitted_at) - a.end_at))::float8
                       as delay_s
            from pass_classifications c
            join assignments a
              on a.assignment_id = c.evidence -> 'report' ->> 'assignment_id'
            join observations o on o.assignment_id = a.assignment_id
            where c.method = %s and c.config_sha256 = %s
              and c.window_end >= %s and c.window_end < %s
            group by c.classification_id, c.simulated, a.end_at
            order by c.classification_id
            """,
            (method, config_sha256, start, end),
        )
        return cur.fetchall()

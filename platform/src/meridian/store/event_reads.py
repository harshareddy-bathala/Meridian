"""What just happened across the network, from the records that say so.

One read, a union of five things the platform already records with the time
they happened: a station registered, a pass first reported decoded, a loss
diagnosed, a receive-chain warning raised, and one cleared. Nothing is written
for the feed, and nothing here decides anything; each row is a record that
exists, read back with its time (D-363).

Reference: docs/DECISIONS.md D-085, D-272, D-292, D-363.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["EVENT_KINDS", "EventKey", "ListedEvent", "find_events"]

EVENT_KINDS = (
    "loss_diagnosed",
    "pass_decoded",
    "station_joined",
    "warning_cleared",
    "warning_raised",
)
"""Every kind, in the order they sort among events of one instant."""

_REPORT_LAG = timedelta(days=30)
"""How long before its report a decoded reception may have started.

MSP refuses an observation that started more than thirty days before it
arrives (D-013), so a report submitted since ``since`` started after
``since − 30 days``. The bound is on the partition column, so the hypertable
reads only the chunks that can hold one."""


@dataclass(frozen=True, slots=True)
class EventKey:
    """Where a page of events ends: the last event's time, kind and record."""

    at: datetime
    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class ListedEvent:
    """One thing that happened, with the record it came from."""

    at: datetime
    kind: str
    ref: str
    """The record's own id: the station's, the observation's, the diagnosis's
    or the warning's, by kind."""

    station_id: str
    simulated: bool
    satellite_id: str | None
    assignment_id: str | None
    cause: str | None


def find_events(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    *,
    since: datetime,
    diagnosed_under: tuple[str, bytes],
    watched_under: tuple[str, bytes],
    after: EventKey | None,
    limit: int,
) -> list[ListedEvent]:
    """Events from ``since`` on, newest first.

    Args:
        conn: An open connection. Read-only.
        since: The earliest instant read.
        diagnosed_under: The deployment's diagnosis method and hash. Another
            configuration's diagnoses are its own record, and a pass is read at
            its latest revision only, as ``/losses`` reads it (D-272).
        watched_under: The deployment's watch method and hash (D-292).
        after: The last event already served, or None for the first page.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Events in descending ``(at, kind, ref)`` order, a total order, so a page
        boundary falls between two events and never inside one. A soft-deleted
        station's events are left out, as every public list leaves them out.
    """
    diagnosis_method, diagnosis_sha = diagnosed_under
    watch_method, watch_sha = watched_under
    with conn.cursor(row_factory=class_row(ListedEvent)) as cur:
        cur.execute(
            """
            select e.at, e.kind, e.ref, e.station_id, e.simulated,
                   e.satellite_id, e.assignment_id, e.cause
            from (
                select s.registered_at as at, 'station_joined' as kind,
                       s.station_id as ref, s.station_id, s.simulated,
                       null::text as satellite_id, null::text as assignment_id,
                       null::text as cause
                from stations s
                where s.registered_at >= %(since)s
                union all
                select o.submitted_at, 'pass_decoded', o.observation_id,
                       o.station_id, (o.simulated or s.simulated), o.satellite_id,
                       o.assignment_id, null
                from observations o
                join stations s on s.station_id = o.station_id
                where o.outcome = 'decoded'
                  and o.started_at >= %(since)s - %(lag)s
                  and o.submitted_at >= %(since)s
                  and not exists (
                      select 1 from observations earlier
                      where earlier.assignment_id = o.assignment_id
                        and earlier.outcome = 'decoded'
                        and earlier.revision < o.revision
                  )
                union all
                select d.computed_at, 'loss_diagnosed', d.diagnosis_id::text,
                       d.station_id, d.simulated, c.satellite_id, d.assignment_id,
                       d.cause
                from loss_diagnoses d
                join pass_classifications c on c.classification_id = d.classification_id
                where d.method = %(diagnosis_method)s
                  and d.config_sha256 = %(diagnosis_sha)s
                  and d.computed_at >= %(since)s
                  and not exists (
                      select 1 from loss_diagnoses later
                      where later.assignment_id = d.assignment_id
                        and later.method = d.method
                        and later.config_sha256 = d.config_sha256
                        and coalesce(later.revision, 0) > coalesce(d.revision, 0)
                  )
                union all
                select w.raised_at, 'warning_raised', w.id::text, w.station_id,
                       w.simulated, null, null, null
                from receive_chain_warnings w
                where w.method = %(watch_method)s and w.config_sha256 = %(watch_sha)s
                  and w.raised_at >= %(since)s
                union all
                select w.cleared_at, 'warning_cleared', w.id::text, w.station_id,
                       w.simulated, null, null, null
                from receive_chain_warnings w
                where w.method = %(watch_method)s and w.config_sha256 = %(watch_sha)s
                  and w.cleared_at >= %(since)s
            ) e
            join stations alive on alive.station_id = e.station_id
            where alive.deleted_at is null
              and (
                  %(after_at)s::timestamptz is null
                  or (e.at, e.kind, e.ref)
                     < (%(after_at)s::timestamptz, %(after_kind)s::text,
                        %(after_ref)s::text)
              )
            order by e.at desc, e.kind desc, e.ref desc
            limit %(limit)s
            """,
            {
                "since": since,
                "lag": _REPORT_LAG,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha": diagnosis_sha,
                "watch_method": watch_method,
                "watch_sha": watch_sha,
                "after_at": after.at if after else None,
                "after_kind": after.kind if after else None,
                "after_ref": after.ref if after else None,
                "limit": limit,
            },
        )
        return cur.fetchall()

"""Owner reports — one row per report, and every attempt to deliver it.

Reads and writes ``report_deliveries`` and ``report_delivery_attempts``
(``deploy/migrations/sql/0031_owner_reports.sql``).

**Written once.** A report is queued once per subject and channel: queuing a
pass or a week that is already queued writes nothing and returns ``None``. The
rendered text is not stored; the row keeps the rows it was read from, the
template version and both hashes, which is what a regeneration needs (D-302).

**No recipient is held** (D-107, D-300). The station is the only thing a row
says about who the report is for.

**``simulated`` is never the caller's.** A pass report takes it from its
assignment and a weekly report from its station, and the database refuses a
simulated report on any channel but the sink (D-301).

**Attempts are appended.** A report is due while it has no attempt, or while
its latest attempt failed and its retry time has come; one sent or abandoned
attempt ends it, and the database holds it to one.

Reference: docs/DECISIONS.md D-098, D-104, D-107, D-300 to D-302, D-305.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "DELIVERY_STATES",
    "DeliveryCount",
    "NewPassReport",
    "NewWeeklyReport",
    "StoredAttempt",
    "StoredDelivery",
    "count_deliveries",
    "due_deliveries",
    "find_attempts",
    "find_deliveries",
    "find_delivery",
    "queue_pass_report",
    "queue_weekly_report",
    "record_attempt",
]


@dataclass(frozen=True, slots=True)
class NewPassReport:
    """A report on one assignment's pass, as queued."""

    assignment_id: str
    channel: str
    template_version: str
    inputs: dict[str, object]
    inputs_sha256: bytes
    content_sha256: bytes


@dataclass(frozen=True, slots=True)
class NewWeeklyReport:
    """A report on one station's week, as queued."""

    station_id: str
    period_start: datetime
    """Monday 00:00 UTC."""

    channel: str
    template_version: str
    inputs: dict[str, object]
    inputs_sha256: bytes
    content_sha256: bytes


@dataclass(frozen=True, slots=True)
class _Id:
    id: int


def queue_pass_report(conn: Connection, row: NewPassReport) -> int | None:
    """Queue a pass report, unless this pass is already queued on this channel.

    Returns:
        The new report's id, or ``None`` when one was already queued, or when
        the assignment does not exist.
    """
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute(
            """
            insert into report_deliveries (
                station_id, kind, assignment_id, channel, template_version,
                inputs_json, inputs_sha256, content_sha256, simulated)
            select a.station_id, 'pass', a.assignment_id, %(channel)s,
                   %(template)s, %(inputs)s, %(inputs_sha)s, %(content_sha)s,
                   a.simulated
            from assignments a where a.assignment_id = %(assignment)s
            on conflict (assignment_id, channel) where kind = 'pass' do nothing
            returning id
            """,
            {
                "assignment": row.assignment_id,
                "channel": row.channel,
                "template": row.template_version,
                "inputs": Jsonb(row.inputs),
                "inputs_sha": row.inputs_sha256,
                "content_sha": row.content_sha256,
            },
        )
        written = cur.fetchone()
    return None if written is None else written.id


def queue_weekly_report(conn: Connection, row: NewWeeklyReport) -> int | None:
    """Queue a weekly report, unless this week is already queued on this channel.

    Returns:
        The new report's id, or ``None`` when one was already queued, or when
        the station does not exist.
    """
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute(
            """
            insert into report_deliveries (
                station_id, kind, period_start, channel, template_version,
                inputs_json, inputs_sha256, content_sha256, simulated)
            select s.station_id, 'weekly', %(period)s, %(channel)s,
                   %(template)s, %(inputs)s, %(inputs_sha)s, %(content_sha)s,
                   s.simulated
            from stations s where s.station_id = %(station)s
            on conflict (station_id, period_start, channel) where kind = 'weekly'
            do nothing
            returning id
            """,
            {
                "station": row.station_id,
                "period": row.period_start,
                "channel": row.channel,
                "template": row.template_version,
                "inputs": Jsonb(row.inputs),
                "inputs_sha": row.inputs_sha256,
                "content_sha": row.content_sha256,
            },
        )
        written = cur.fetchone()
    return None if written is None else written.id


@dataclass(frozen=True, slots=True)
class StoredDelivery:
    """One queued report."""

    id: int
    station_id: str
    kind: str
    assignment_id: str | None
    period_start: datetime | None
    channel: str
    template_version: str
    inputs_json: dict[str, object]
    inputs_sha256: bytes
    content_sha256: bytes
    queued_at: datetime
    simulated: bool
    attempts: int
    state: str
    """``queued``, ``retrying``, ``sent`` or ``abandoned``."""

    last_error: str | None
    """The latest attempt's error class, or ``None``."""


_DELIVERIES = """
    select d.id, d.station_id, d.kind, d.assignment_id, d.period_start,
           d.channel, d.template_version, d.inputs_json, d.inputs_sha256,
           d.content_sha256, d.queued_at, d.simulated,
           coalesce(t.attempts, 0)::integer as attempts,
           case when l.outcome is null then 'queued'
                when l.outcome = 'failed' then 'retrying'
                else l.outcome end as state,
           l.error_class as last_error
    from report_deliveries d
    left join lateral (
        select outcome, error_class, next_attempt_at from report_delivery_attempts
        where delivery_id = d.id
        order by attempted_at desc, id desc limit 1
    ) l on true
    left join lateral (
        select count(*) as attempts from report_delivery_attempts
        where delivery_id = d.id
    ) t on true
"""


DELIVERY_STATES = ("queued", "retrying", "sent", "abandoned")


@dataclass(frozen=True, slots=True)
class DeliveryCount:
    """How many reports of one population are in one state."""

    simulated: bool
    state: str
    reports: int


def count_deliveries(conn: Connection) -> list[DeliveryCount]:
    """Reports by population and state; a population with none is absent."""
    with conn.cursor(row_factory=class_row(DeliveryCount)) as cur:
        cur.execute(
            f"""
            select simulated, state, count(*)::integer as reports
            from ({_DELIVERIES}) counted
            group by simulated, state
            order by simulated, state
            """
        )
        return cur.fetchall()


def find_delivery(conn: Connection, delivery_id: int) -> StoredDelivery | None:
    """One report by its id."""
    with conn.cursor(row_factory=class_row(StoredDelivery)) as cur:
        cur.execute(_DELIVERIES + " where d.id = %s", (delivery_id,))
        return cur.fetchone()


def find_deliveries(
    conn: Connection, *, station_id: str | None = None, limit: int = 50
) -> list[StoredDelivery]:
    """The most recently queued reports, newest first, for one station or all."""
    with conn.cursor(row_factory=class_row(StoredDelivery)) as cur:
        cur.execute(
            _DELIVERIES
            + """
            where %(station)s::text is null or d.station_id = %(station)s
            order by d.queued_at desc, d.id desc
            limit %(limit)s
            """,
            {"station": station_id, "limit": limit},
        )
        return cur.fetchall()


def due_deliveries(
    conn: Connection, now: datetime, *, limit: int = 50
) -> list[StoredDelivery]:
    """Reports to try now, oldest queued first.

    A report is due while nothing has been attempted, or while its latest
    attempt failed and the time it named to try again has come. A sent or
    abandoned report is never due again.
    """
    with conn.cursor(row_factory=class_row(StoredDelivery)) as cur:
        cur.execute(
            _DELIVERIES
            + """
            where l.outcome is null
               or (l.outcome = 'failed' and l.next_attempt_at <= %(now)s)
            order by d.queued_at, d.id
            limit %(limit)s
            """,
            {"now": now, "limit": limit},
        )
        return cur.fetchall()


def record_attempt(  # noqa: PLR0913 — one attempt's columns, each by name
    conn: Connection,
    delivery_id: int,
    *,
    attempted_at: datetime,
    outcome: str,
    error_class: str | None = None,
    next_attempt_at: datetime | None = None,
) -> int:
    """Append one attempt to deliver a report.

    Args:
        conn: An open connection.
        delivery_id: The report attempted.
        attempted_at: When it was attempted.
        outcome: ``sent``, ``failed`` or ``abandoned``.
        error_class: A short code for why it failed, never a server's words;
            ``None`` exactly when it was sent.
        next_attempt_at: When to try again, for a failed attempt only.

    Returns:
        The attempt's id.

    Raises:
        psycopg.errors.UniqueViolation: The report was already sent or
            abandoned.
        psycopg.errors.CheckViolation: The attempt's parts do not fit its
            outcome.
    """
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute(
            """
            insert into report_delivery_attempts (
                delivery_id, attempted_at, outcome, error_class, next_attempt_at)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (delivery_id, attempted_at, outcome, error_class, next_attempt_at),
        )
        written = cur.fetchone()
    if written is None:  # pragma: no cover — an insert always returns its row
        raise LookupError(f"no attempt written for report {delivery_id}")
    return written.id


@dataclass(frozen=True, slots=True)
class StoredAttempt:
    """One attempt to deliver a report."""

    id: int
    delivery_id: int
    attempted_at: datetime
    outcome: str
    error_class: str | None
    next_attempt_at: datetime | None


def find_attempts(conn: Connection, delivery_id: int) -> list[StoredAttempt]:
    """Every attempt to deliver one report, oldest first."""
    with conn.cursor(row_factory=class_row(StoredAttempt)) as cur:
        cur.execute(
            """
            select id, delivery_id, attempted_at, outcome, error_class,
                   next_attempt_at
            from report_delivery_attempts
            where delivery_id = %s
            order by attempted_at, id
            """,
            (delivery_id,),
        )
        return cur.fetchall()

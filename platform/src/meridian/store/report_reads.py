"""What a pass report reads: which passes are due, and the rows each one names.

Reads ``assignments``, ``passes``, ``satellites``, ``stations``,
``observations``, ``reception_verdicts``, ``loss_diagnoses``,
``pass_classifications`` and ``report_deliveries``. Writes nothing. A week's
reads are :mod:`meridian.store.week_reads`.

**A report names its rows** (D-302). Choosing what a report is about reads the
latest of everything; reading it reads exactly the rows chosen, by key, so a
verdict or a diagnosis written afterwards never changes a report already sent.
Every row read is append-only, and none has a retention policy (D-178).

**A pass is due** once its window has closed and Stage 20 has classified it,
and then when what it will say is settled: its loss has a diagnosis, or it
decoded and its verdict is written and above the partial threshold, or none
of that came within the wait, in which case it is reported with what there is.
An expired or revoked assignment is a decline, never a reception, and is never
reported (D-008).

Rows only; the facts a template reads are built from them in
:mod:`meridian.notifications.reading`, so the store never imports the module
that composes reports (D-102).

Reference: docs/DECISIONS.md D-008, D-104, D-178, D-302.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "DuePass",
    "PassRow",
    "ReceptionKey",
    "find_due_passes",
    "read_pass",
]


@dataclass(frozen=True, slots=True)
class ReceptionKey:
    """The rows one reception is read from: what a report names."""

    assignment_id: str
    revision: int | None
    verdict_method: str | None
    diagnosis_id: int | None


@dataclass(frozen=True, slots=True)
class DuePass:
    """A pass ready to report on one channel."""

    station_id: str
    channel: str
    key: ReceptionKey


@dataclass(frozen=True, slots=True)
class _DueRow:
    station_id: str
    channel: str
    assignment_id: str
    revision: int | None
    verdict_method: str | None
    diagnosis_id: int | None


LATEST_ROWS = """
    left join observations_current o on o.assignment_id = a.assignment_id
    left join reception_verdicts v
           on v.assignment_id = o.assignment_id
          and v.revision = o.revision
          and v.method = %(verdict_method)s
    left join lateral (
        select d.diagnosis_id from loss_diagnoses d
        where d.assignment_id = a.assignment_id
          and d.revision is not distinct from o.revision
          and d.method = %(diagnosis_method)s
          and d.config_sha256 = %(diagnosis_sha256)s
        order by d.computed_at desc, d.diagnosis_id desc
        limit 1
    ) d on true
"""
"""The latest observation, verdict and diagnosis of an assignment ``a``."""

HELD_AND_CLASSIFIED = """
    a.decision = 'scheduled'
    and a.revoked_reason is null
    and a.state not in ('expired', 'revoked')
    and exists (
        select 1 from pass_classifications c
        where a.assignment_id = any (c.assignment_ids)
          and c.method = %(classification_method)s
          and c.config_sha256 = %(classification_sha256)s)
"""
"""An assignment the station held, whose pass Stage 20 has classified."""


def find_due_passes(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    channels: dict[str, str],
    now: datetime,
    *,
    wait_s: int,
    classification_method: str,
    classification_sha256: bytes,
    diagnosis_method: str,
    diagnosis_sha256: bytes,
    verdict_method: str | None,
    limit: int = 200,
) -> list[DuePass]:
    """Passes of the given stations to report on now, oldest window first.

    Args:
        conn: An open connection.
        channels: Each reporting station's channel, by station id.
        now: The round's instant.
        wait_s: How long after its window a pass is reported with whatever is
            stored, rather than waiting longer for a verdict or a diagnosis.
        classification_method: The deployment's classification method.
        classification_sha256: Its configuration's hash.
        diagnosis_method: The deployment's diagnosis method.
        diagnosis_sha256: Its ``[diagnosis]`` table's hash.
        verdict_method: The deployed verdict model's method, or ``None``.
        limit: The most to return.
    """
    if not channels:
        return []
    stations, chosen = zip(*sorted(channels.items()), strict=True)
    with conn.cursor(row_factory=class_row(_DueRow)) as cur:
        cur.execute(
            f"""
            with reporting (station_id, channel) as (
                select * from unnest(%(stations)s::text[], %(channels)s::text[]))
            select a.station_id, r.channel, a.assignment_id, o.revision,
                   v.method as verdict_method, d.diagnosis_id
            from reporting r
            join assignments a on a.station_id = r.station_id
            {LATEST_ROWS}
            where {HELD_AND_CLASSIFIED}
              and a.end_at <= %(now)s
              and not exists (
                  select 1 from report_deliveries q
                  where q.kind = 'pass' and q.assignment_id = a.assignment_id
                    and q.channel = r.channel)
              and (d.diagnosis_id is not null
                   or (o.outcome = 'decoded'
                       and (%(verdict_method)s::text is null
                            or v.probability_usable >= v.partial_below))
                   or a.end_at <= %(now)s - make_interval(secs => %(wait_s)s))
            order by a.end_at, a.assignment_id
            limit %(limit)s
            """,
            {
                "stations": list(stations),
                "channels": list(chosen),
                "now": now,
                "wait_s": wait_s,
                "classification_method": classification_method,
                "classification_sha256": classification_sha256,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha256": diagnosis_sha256,
                "verdict_method": verdict_method,
                "limit": limit,
            },
        )
        rows = cur.fetchall()
    return [
        DuePass(
            station_id=row.station_id,
            channel=row.channel,
            key=ReceptionKey(
                row.assignment_id, row.revision, row.verdict_method, row.diagnosis_id
            ),
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class PassRow:
    """One pass report's rows, read by the keys it names."""

    station_id: str
    station_name: str
    simulated: bool
    satellite_id: str
    satellite_name: str
    start_at: datetime
    end_at: datetime
    max_elevation_deg: float
    outcome: str | None
    peak_snr_db: float | None
    frames_decoded: int | None
    probability_usable: float | None
    partial_below: float | None
    verdict_method: str | None
    cause: str | None
    candidates_json: list[dict[str, object]] | None
    diagnosis_method: str | None


def read_pass(conn: Connection, key: ReceptionKey) -> PassRow:
    """Exactly the rows ``key`` names.

    Raises:
        LookupError: A named row is not there.
    """
    with conn.cursor(row_factory=class_row(PassRow)) as cur:
        cur.execute(
            """
            select a.station_id, s.name as station_name, a.simulated,
                   p.satellite_id, sat.name as satellite_name, a.start_at,
                   a.end_at, p.max_elevation_deg,
                   o.outcome, o.peak_snr_db, o.frames_decoded,
                   v.probability_usable, v.partial_below,
                   v.method as verdict_method,
                   d.cause, d.candidates_json, d.method as diagnosis_method
            from assignments a
            join stations s on s.station_id = a.station_id
            join passes p on p.id = a.pass_id
            join satellites sat on sat.satellite_id = p.satellite_id
            left join observations o
                   on o.assignment_id = a.assignment_id
                  and o.revision = %(revision)s
            left join reception_verdicts v
                   on v.assignment_id = a.assignment_id
                  and v.revision = %(revision)s
                  and v.method = %(verdict_method)s
            left join loss_diagnoses d
                   on d.diagnosis_id = %(diagnosis_id)s
                  and d.assignment_id = a.assignment_id
            where a.assignment_id = %(assignment_id)s
            """,
            {
                "assignment_id": key.assignment_id,
                "revision": key.revision,
                "verdict_method": key.verdict_method,
                "diagnosis_id": key.diagnosis_id,
            },
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(
            f"a report names assignment {key.assignment_id}, which is not stored"
        )
    require(key.revision is None or row.outcome is not None, f"{key} observation")
    require(
        key.verdict_method is None or row.probability_usable is not None,
        f"{key} verdict",
    )
    require(key.diagnosis_id is None or row.cause is not None, f"{key} diagnosis")
    return row


def require(held: bool, what: str) -> None:
    """Refuse a report whose named row is not stored."""
    if not held:
        raise LookupError(f"a report names {what}, which is not stored")

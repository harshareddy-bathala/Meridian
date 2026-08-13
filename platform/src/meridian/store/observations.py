"""Observations — the SQL layer behind MSP §4.4.

Reads and writes ``observations`` (``deploy/migrations/sql/0005_observations.sql``)
and reads the one assignment row an incoming observation has to be checked
against. Like the rest of ``meridian.store``, this module persists and retrieves;
whether a submission is a new revision, a duplicate or a correction is decided by
:mod:`meridian.observations.ingest`, which composes these calls inside one
transaction.

The table is append-only (D-015). Nothing here updates or deletes a row: a
correction is :func:`insert_observation` at ``revision + 1``, and the
``observations_current`` view exposes the highest revision per assignment.

Three columns are deliberately absent from :class:`NewObservation` and are
therefore impossible to pass in — ``revision`` and ``content_sha256`` are decided
by the ingest service, ``submitted_at`` and ``observation_id`` by the database.
``simulated`` is present but is the station's registry provenance rather than
anything it sent, read by the caller through
``store.stations.find_station_provenance`` (D-048).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "AssignmentForReport",
    "DopplerSample",
    "LatestObservation",
    "NewObservation",
    "find_latest_observation",
    "insert_observation",
    "lock_assignment_for_report",
]


@dataclass(frozen=True, slots=True)
class DopplerSample:
    """One measured frequency offset, at the instant it was measured.

    MSP §4.4 puts at most 512 of these on an observation (D-032). They are the
    raw measurement rather than a fitted curve, because the residual between the
    samples and a model is what the orbit-uncertainty analysis reads.
    """

    sampled_at: datetime
    offset_hz: int
    """Observed minus nominal. Positive while the satellite is approaching."""


@dataclass(frozen=True, slots=True)
class NewObservation:
    """One MSP §4.4 observation, in insertable form.

    Bundled rather than passed as parameters, past CLAUDE.local.md's cap of four,
    for the same reason as ``store.heartbeats.NewHeartbeat``.

    ``satellite_id`` and ``simulated`` are derived by the caller — the first from
    the assignment's pass, the second from the station's registration record —
    and never taken from the payload, which is why neither can be set by a
    station sending an unexpected field.
    """

    assignment_id: str
    station_id: str
    satellite_id: str

    started_at: datetime
    ended_at: datetime

    outcome: str
    """One of MSP §4.4's five values, pinned by a ``CHECK`` (D-010)."""

    signal_detected: bool
    first_detection_at: datetime | None
    """Present exactly when ``signal_detected``; the table enforces the pair.

    Its difference from the predicted acquisition time is this project's primary
    measurement of orbital data quality, which is why a null here is a real loss
    rather than a missing nicety.
    """

    peak_snr_db: float | None
    doppler_samples: tuple[DopplerSample, ...] | None
    products: tuple[Mapping[str, object], ...]
    """MSP §4.4's ``products`` array, stored verbatim (D-018, D-029).

    Metadata only — ``kind``, ``uri``, ``sha256`` and whatever else the product
    type warrants. Untyped beyond a mapping on purpose: a schema invented before
    the first receiver exists would reject a station we have not built yet.
    """

    client_notes: str | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class AssignmentForReport:
    """What ingest needs to know about the assignment an observation names."""

    station_id: str
    """Whose assignment it is. A submission from anyone else is ``not_owner``."""

    state: str
    satellite_id: str
    """Derived through the assignment's pass, never read from the payload."""


@dataclass(frozen=True, slots=True)
class LatestObservation:
    """The current revision for one assignment, or the absence of one."""

    revision: int
    content_sha256: bytes
    observation_id: str


def lock_assignment_for_report(
    conn: Connection, assignment_id: str
) -> AssignmentForReport | None:
    """Read the assignment an observation names, locking it until commit.

    Args:
        conn: An open connection **with a transaction already open**. The lock
            this takes is released at commit, so calling without one would
            release it before the caller had used it.
        assignment_id: The id from the submitted body.

    Returns:
        The assignment, or ``None`` when no such id exists — which the caller
        answers as MSP §6's ``unknown_assignment``.

    Note:
        ``for update`` is what stops two submissions for one assignment
        appending two revisions of the same thing (D-071). The row is locked
        rather than a table or an advisory key because ingest has to read it
        anyway — for the ownership check and for ``satellite_id`` — so
        serialising costs one clause on a query that was already required.

        ``of a`` restricts the lock to ``assignments``: ``passes`` is joined for
        one column and locking it would serialise unrelated work on the same
        pass for no benefit.
    """
    with conn.cursor(row_factory=class_row(AssignmentForReport)) as cur:
        cur.execute(
            """
            select a.station_id, a.state, p.satellite_id
            from assignments a
            join passes p on p.id = a.pass_id
            where a.assignment_id = %s
            for update of a
            """,
            (assignment_id,),
        )
        return cur.fetchone()


def find_latest_observation(
    conn: Connection, assignment_id: str
) -> LatestObservation | None:
    """The current revision for ``assignment_id``, or ``None`` if it has none.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        assignment_id: Which assignment's lineage to read the head of.

    Returns:
        The highest revision, with the hash a resubmission is compared against
        and the public id an unchanged resubmission is answered with.

    Note:
        Reads ``observations`` directly rather than ``observations_current``.
        The view exists for readers who want the current row and nothing else;
        this caller wants three columns of it and the ordering that produced it,
        and going through the view would hide the ``revision desc`` that the
        next insert increments.
    """
    with conn.cursor(row_factory=class_row(LatestObservation)) as cur:
        cur.execute(
            """
            select revision, content_sha256, observation_id
            from observations
            where assignment_id = %s
            order by revision desc
            limit 1
            """,
            (assignment_id,),
        )
        return cur.fetchone()


def insert_observation(
    conn: Connection,
    record: NewObservation,
    *,
    revision: int,
    content_sha256: bytes,
) -> str:
    """Append one observation revision and return the id the platform will ack.

    Args:
        conn: An open connection. This function owns its transaction, which
            nests as a savepoint inside the caller's.
        record: The derived, validated observation.
        revision: 1 for a first report, ``n + 1`` for a correction. Decided by
            :mod:`meridian.observations.ingest` against the current head, under
            the lock :func:`lock_assignment_for_report` took.
        content_sha256: The digest a later resubmission is compared against
            (D-070). Computed from ``record``, never supplied by a station.

    Returns:
        ``observation_id`` — ``ob_`` and twelve hex characters, generated by the
        database from ``(assignment_id, revision)`` (D-027).

    Note:
        ``returning`` rather than a second read: the id is a stored generated
        column, so the insert already knows it, and D-015 put this whole path on
        the budget of writing as little as possible.

        ``submitted_at`` is left to the column's ``default now()`` — the
        platform's clock, not the station's. MSP §6 requires queued observations
        to keep their original timestamps, so ``started_at`` and ``ended_at``
        arrive from the station and the difference between them and this column
        *is* the submission delay.

        **The primary key does not by itself stop two rows sharing a revision.**
        It is ``(assignment_id, revision, started_at)``, because Timescale
        requires the partitioning column in every unique key (D-013), so a
        second insert at the same revision with a different ``started_at`` would
        be accepted. What prevents it is the caller holding the assignment row
        lock across the read and the insert (D-071).
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into observations
                (assignment_id, revision, started_at, ended_at,
                 station_id, satellite_id, outcome,
                 signal_detected, first_detection_at, peak_snr_db,
                 doppler_samples, products_json, client_notes,
                 simulated, content_sha256)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s, %s)
            returning observation_id
            """,
            (
                record.assignment_id,
                revision,
                record.started_at,
                record.ended_at,
                record.station_id,
                record.satellite_id,
                record.outcome,
                record.signal_detected,
                record.first_detection_at,
                record.peak_snr_db,
                _doppler_json(record.doppler_samples),
                json.dumps(list(record.products)),
                record.client_notes,
                record.simulated,
                content_sha256,
            ),
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover — an insert that returns nothing
            raise RuntimeError("insert into observations returned no row")
        return str(row[0])


def _doppler_json(samples: Sequence[DopplerSample] | None) -> str | None:
    """The Doppler array as MSP §4.4 shapes it, or null when none was sent.

    Stored in the wire's shape — ``t`` and ``offset_hz`` — so an analysis reading
    the column and an implementer reading the specification see the same thing.
    Instants render through ``isoformat``; the ``Z``-suffixed form D-070 hashes
    is that decision's own, and the two are independent because the hash is
    defined over a rendering of the record rather than over this column.
    """
    if samples is None:
        return None
    return json.dumps(
        [
            {"t": one.sampled_at.isoformat(), "offset_hz": one.offset_hz}
            for one in samples
        ]
    )

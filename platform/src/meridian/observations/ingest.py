"""Accepting one observation: what is checked, what is derived, what is written.

The service behind MSP §4.4. It takes the facts a station submitted, resolves the
two the station is not trusted for, decides whether the submission is a first
report, an unchanged retry or a correction, and writes at most one row.

It speaks no HTTP: the errors below are domain errors, and ``meridian.api``
translates them into MSP §6 codes — the same division
:class:`~meridian.registry.psycopg_registry.PsycopgRegistry` uses for
``InvalidInviteError``. It also decides nothing about whether a pass was missed;
that is ``meridian.reliability``'s, and it reads what this module wrote.

:func:`decide_revision` is separated out and pure, because the append-or-not rule
is the one piece of Stage 9 that has to be right and is cheapest to be sure of
away from a database.

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-015, D-027, D-048,
D-070, D-071.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.observations.canonical_body import content_sha256
from meridian.store.assignments import mark_assignment_reported
from meridian.store.observations import (
    AssignmentForReport,
    DopplerSample,
    LatestObservation,
    NewObservation,
    find_latest_observation,
    insert_observation,
    lock_assignment_for_report,
)
from meridian.store.stations import Connection, find_station_provenance

__all__ = [
    "Acknowledgement",
    "NotOwnerError",
    "RevisionDecision",
    "Submission",
    "UnknownAssignmentError",
    "decide_revision",
    "ingest",
]

_log = logging.getLogger(__name__)

EXPIRED = "expired"
"""The one assignment state an arriving observation does not transition (D-071).

D-008 draws no arc out of it, and adding one would erase the record that the
station stopped naming the work — which is the evidence CLAUDE.md rule 7's
distinction between a broken station and an idle one is built from.
"""


class UnknownAssignmentError(Exception):
    """No assignment carries the submitted id. MSP §6's ``unknown_assignment``."""


class NotOwnerError(Exception):
    """The assignment exists and belongs to another station. MSP §6's ``not_owner``."""


@dataclass(frozen=True, slots=True)
class Submission:
    """What a station sent, before anything is derived from the platform's records.

    Deliberately *not* a :class:`~meridian.store.observations.NewObservation`:
    that type carries ``satellite_id`` and ``simulated``, and there is no honest
    value for either until the assignment and the station's registration record
    have been read. Keeping them off this type means a station's payload cannot
    reach those columns even by accident (D-048).
    """

    assignment_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    signal_detected: bool
    first_detection_at: datetime | None
    peak_snr_db: float | None
    doppler_samples: tuple[DopplerSample, ...] | None
    products: tuple[Mapping[str, object], ...]
    client_notes: str | None


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    """Whether this submission writes anything, and what the acknowledgement says."""

    revision: int
    """The revision that is current once this submission has been handled."""

    superseded: bool
    """MSP §4.4: the platform already held an observation and this replaced it."""

    existing_observation_id: str | None
    """Set only for an unchanged retry, whose acknowledgement is the original's.

    ``None`` means write the row. Carrying the id rather than a separate
    ``should_write`` flag makes the contradictory pair — "write it, and here is
    the id it already has" — impossible to express.
    """


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    """MSP §4.4's three-field response."""

    observation_id: str
    assignment_id: str
    superseded: bool


def decide_revision(
    latest: LatestObservation | None, digest: bytes
) -> RevisionDecision:
    """Compare a submission against the current revision and choose what happens.

    Args:
        latest: The head of this assignment's lineage, or ``None`` if it has
            none yet.
        digest: This submission's content hash (D-070).

    Returns:
        The revision to write, whether the acknowledgement reports a
        supersession, and — for an unchanged retry only — the existing id to
        answer with.

    Note:
        **An unchanged retry writes nothing and reports ``superseded: false``.**
        It returns the original acknowledgement unaltered, because from the
        station's point of view nothing happened: this is the queued-reconnection
        case of MSP §6, where the station is retrying because it never saw the
        first answer.

        **A resubmission of an older body after a correction appends again.** If
        revision 1 said one thing, revision 2 corrected it, and the station then
        re-sends the revision 1 body, that differs from *current* and so becomes
        revision 3. Comparing against the whole lineage instead would make the
        station's latest statement lose to one it had already withdrawn, and
        append-only means the earlier revisions are still there to show what
        happened.
    """
    if latest is None:
        return RevisionDecision(
            revision=1, superseded=False, existing_observation_id=None
        )
    if latest.content_sha256 == digest:
        return RevisionDecision(
            revision=latest.revision,
            superseded=False,
            existing_observation_id=latest.observation_id,
        )
    return RevisionDecision(
        revision=latest.revision + 1, superseded=True, existing_observation_id=None
    )


def ingest(
    conn: Connection, submission: Submission, *, station_id: str
) -> Acknowledgement:
    """Accept one observation from an authenticated station.

    Args:
        conn: An open connection **with a transaction already open**. The
            assignment lock taken here has to outlive this call, and the
            acknowledgement must not describe a state the caller then rolls back.
        submission: The validated body.
        station_id: The identity the bearer token authenticated as — never the
            ``station_id`` in the body, which the caller has already compared.

    Returns:
        MSP §4.4's acknowledgement.

    Raises:
        UnknownAssignmentError: No assignment carries this id.
        NotOwnerError: The assignment was issued to a different station.
    """
    assignment = _resolve_assignment(conn, submission.assignment_id, station_id)
    record = _derive_record(conn, submission, assignment, station_id)

    digest = content_sha256(record)
    latest = find_latest_observation(conn, submission.assignment_id)
    decision = decide_revision(latest, digest)
    observation_id = _write_unless_unchanged(conn, record, decision, digest)
    _report_assignment(conn, assignment, record)

    return Acknowledgement(
        observation_id=observation_id,
        assignment_id=submission.assignment_id,
        superseded=decision.superseded,
    )


def _resolve_assignment(
    conn: Connection, assignment_id: str, station_id: str
) -> AssignmentForReport:
    """Find the assignment, prove it belongs to this station, and lock it."""
    assignment = lock_assignment_for_report(conn, assignment_id)
    if assignment is None:
        raise UnknownAssignmentError(assignment_id)
    if assignment.station_id != station_id:
        raise NotOwnerError(assignment_id)
    return assignment


def _derive_record(
    conn: Connection,
    submission: Submission,
    assignment: AssignmentForReport,
    station_id: str,
) -> NewObservation:
    """Fill in the two fields a station is not trusted to supply.

    ``satellite_id`` comes from the pass the assignment was cut from, and
    ``simulated`` from the station's registration record (D-048). Neither is read
    from the payload — MSP §4.4 puts no such field on the wire, and this is what
    keeps that true if a later revision adds one.
    """
    provenance = find_station_provenance(conn, station_id)
    if provenance is None:  # pragma: no cover — the token just resolved to it
        raise NotOwnerError(station_id)

    return NewObservation(
        assignment_id=submission.assignment_id,
        station_id=station_id,
        satellite_id=assignment.satellite_id,
        started_at=submission.started_at,
        ended_at=submission.ended_at,
        outcome=submission.outcome,
        signal_detected=submission.signal_detected,
        first_detection_at=submission.first_detection_at,
        peak_snr_db=submission.peak_snr_db,
        doppler_samples=submission.doppler_samples,
        products=submission.products,
        client_notes=submission.client_notes,
        simulated=provenance.simulated,
    )


def _write_unless_unchanged(
    conn: Connection,
    record: NewObservation,
    decision: RevisionDecision,
    digest: bytes,
) -> str:
    """Append the revision, or answer an unchanged retry without writing."""
    if decision.existing_observation_id is not None:
        return decision.existing_observation_id
    return insert_observation(
        conn, record, revision=decision.revision, content_sha256=digest
    )


def _report_assignment(
    conn: Connection, assignment: AssignmentForReport, record: NewObservation
) -> None:
    """Move the assignment to ``reported``, unless it has already expired (D-071).

    An expired assignment keeps its state: it records that the station stopped
    naming the work, and an observation arriving afterwards does not undo that.
    The pair is an anomaly the reliability layer needs to see, so it is logged
    rather than smoothed over.
    """
    if assignment.state == EXPIRED:
        _log.warning(
            "station %s submitted an observation for %s, which had already"
            " expired; the observation is stored and the assignment is left"
            " expired (D-071)",
            record.station_id,
            record.assignment_id,
        )
        return
    mark_assignment_reported(
        conn, station_id=record.station_id, assignment_id=record.assignment_id
    )

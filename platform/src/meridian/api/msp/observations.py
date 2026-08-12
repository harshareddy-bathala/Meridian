"""``POST /msp/v0/observations`` — what a station reports after every attempt.

Validates the body, refuses one whose ``started_at`` could not belong to a real
pass, and hands the rest to :mod:`meridian.observations.ingest`, which owns every
decision about ownership, provenance, revisions and the assignment's state. This
module translates that service's domain errors into MSP §6 codes and nothing
else.

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-013, D-015, D-027,
D-071, D-072.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_authenticated_station_id, get_connection
from meridian.api.errors import MALFORMED, NOT_OWNER, UNKNOWN_ASSIGNMENT, MspError
from meridian.api.models.observation import ObservationAckBody, ObservationRequestBody
from meridian.observations.ingest import (
    Acknowledgement,
    NotOwnerError,
    UnknownAssignmentError,
    ingest,
)
from meridian.store.stations import Connection

__all__ = ["router"]

_log = logging.getLogger(__name__)

router = APIRouter()

OLDEST_ACCEPTED_START = timedelta(days=30)
"""How far back a ``started_at`` may be (D-013).

``observations`` partitions on ``started_at``, so a station with a dead clock
would otherwise place a chunk in 1970 that every retention and compression
policy then mishandles forever. Thirty days is generous against MSP §6's queued
submissions — a station offline for a month has lost its passes anyway — and
finite, which is the property the partitioning needs.
"""

FURTHEST_ACCEPTED_START = timedelta(hours=1)
"""How far ahead a ``started_at`` may be.

A pass cannot start after the report of it arrives, so anything in the future is
clock skew rather than data. An hour of tolerance absorbs a station whose clock
is wrong without accepting one that is wrong by a year.
"""

STATION_MISMATCH_MESSAGE = "Bearer token does not belong to the station_id in the body."
"""The body names a station the credential does not authenticate as.

Naming the disagreement rather than echoing either value keeps a token holder
from probing which station ids exist — the same discipline the heartbeat applies.
"""

NOT_OWNER_MESSAGE = "Assignment was not issued to this station."
UNKNOWN_ASSIGNMENT_MESSAGE = "No assignment carries that assignment_id."
IMPLAUSIBLE_START_MESSAGE = (
    "started_at is outside the window the platform accepts; check the station clock."
)


def _require_a_plausible_start(started_at: datetime, now: datetime) -> None:
    """Refuse a ``started_at`` no real pass could have had.

    Args:
        started_at: The instant the station says reception began.
        now: The platform clock, passed in so the rule is testable at a fixed
            moment rather than only in real time.

    Raises:
        MspError: ``malformed`` (400) when the instant falls outside
            ``[now − 30 days, now + 1 hour]``.
    """
    if not now - OLDEST_ACCEPTED_START <= started_at <= now + FURTHEST_ACCEPTED_START:
        raise MspError(MALFORMED, IMPLAUSIBLE_START_MESSAGE)


def _ingest_or_translate(
    conn: Connection, body: ObservationRequestBody, station_id: str
) -> Acknowledgement:
    """Call the service and turn its domain errors into MSP §6 codes.

    Raises:
        MspError: ``unknown_assignment`` (404) or ``not_owner`` (403).
    """
    try:
        return ingest(conn, body.to_submission(), station_id=station_id)
    except UnknownAssignmentError as exc:
        raise MspError(UNKNOWN_ASSIGNMENT, UNKNOWN_ASSIGNMENT_MESSAGE) from exc
    except NotOwnerError as exc:
        _log.warning(
            "station %s submitted an observation for %s, which is not its assignment",
            station_id,
            body.assignment_id,
        )
        raise MspError(NOT_OWNER, NOT_OWNER_MESSAGE) from exc


@router.post("/observations")
def submit_observation(
    body: ObservationRequestBody,
    station_id: str = Depends(get_authenticated_station_id),
    conn: Connection = Depends(get_connection),
) -> ObservationAckBody:
    """Record what a station heard, or did not hear, on one assignment (MSP §4.4).

    **Sent after every attempt, including the ones that produced nothing.** A
    station that was listening and heard nothing has measured something; a
    station that never began has not. The two are different ``outcome`` values
    and must never be conflated, which is why both produce a row here and a
    decline produces none at all.

    Returns:
        ``{"observation_id": ..., "assignment_id": ..., "superseded": ...}`` —
        MSP §4.4's acknowledgement, exactly.

    Raises:
        MspError: ``unauthorized`` (401) from the bearer dependency;
            ``not_owner`` (403) when the body names a different station or the
            assignment belongs to one; ``unknown_assignment`` (404); ``malformed``
            (400) for a ``started_at`` no pass could have had.

    Note:
        Validation, ingest and the assignment transition happen in **one
        transaction**, so the acknowledgement describes a state that has
        committed. It also holds the assignment's row lock across the read of the
        current revision and the write of the next one, which is what stops two
        concurrent submissions appending the same revision twice (D-071).
    """
    if body.station_id != station_id:
        _log.warning(
            "station %s submitted an observation claiming to be %s",
            station_id,
            body.station_id,
        )
        raise MspError(NOT_OWNER, STATION_MISMATCH_MESSAGE)

    _require_a_plausible_start(body.started_at, platform_clock.utc_now())

    with conn.transaction():
        acknowledgement = _ingest_or_translate(conn, body, station_id)

    return ObservationAckBody(
        observation_id=acknowledgement.observation_id,
        assignment_id=acknowledgement.assignment_id,
        superseded=acknowledgement.superseded,
    )

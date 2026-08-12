"""``POST /msp/v0/heartbeat`` — liveness in, and the work a station is due back.

Recording the report, reconciling what the station says it holds, and reading
back what to deliver all happen in **one transaction**, so the response describes
the state this heartbeat just produced. The reconciliation itself is
:mod:`meridian.registry.heartbeat_reconciliation`'s; this module composes it with
the store and MSP §4.2's delivery rules.

Reference: docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-007, D-026, D-035,
D-048, D-067.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_authenticated_station_id, get_connection
from meridian.api.errors import NOT_OWNER, MspError
from meridian.api.models.assignment import (
    MAX_ASSIGNMENTS_PER_RESPONSE,
    AssignmentMessage,
    HeartbeatResponseBody,
)
from meridian.api.models.heartbeat import HeartbeatRequestBody
from meridian.registry.heartbeat_reconciliation import (
    HeldReport,
    LiveAssignments,
    reconcile,
)
from meridian.store.assignments import (
    DueAssignment,
    expire_overdue_assignments,
    find_assignment_ids_by_state,
    find_due_assignments,
    mark_assignment_in_progress,
    mark_assignments_held,
)
from meridian.store.heartbeats import insert_heartbeat, touch_last_heartbeat
from meridian.store.stations import Connection, find_station_provenance

__all__ = ["router"]

_log = logging.getLogger(__name__)

router = APIRouter()

ASSIGNMENT_HORIZON = timedelta(hours=2)
"""How far ahead of ``now`` an assignment is offered (D-026).

Roughly one and a quarter low-Earth orbits: far enough ahead that a station is
never idle waiting for work, near enough that a station holding an assignment
still has current element sets for it, and small enough that D-007's eight slots
are rarely the binding constraint.
"""

NOT_OWNER_MESSAGE = "Bearer token does not belong to the station_id in the body."
"""Fixed text for a body whose ``station_id`` contradicts the credential.

The token is the authoritative identity; §4.2's ``station_id`` is the station
restating who it believes it is. Naming the disagreement rather than echoing
either value keeps a token holder from probing which station ids exist.
"""

LIVE_STATES = ("issued", "held", "in_progress", "reported", "expired")
"""Every state an assignment this station was issued can be in.

Read whole rather than filtered to the two states that transition, because
:class:`LiveAssignments` needs the full set to answer MSP §4.2's protocol-error
row: an id is foreign when it was *never* issued here, not when it is merely
finished.
"""


def _record_heartbeat(
    conn: Connection, body: HeartbeatRequestBody, station_id: str
) -> None:
    """Store one heartbeat and bump the station's last-seen instant.

    Both writes together: a heartbeat row without the ``stations`` bump would
    leave ``liveness()`` reading ``offline`` for a station that had just
    reported, and the bump without the row would lose the listening evidence
    ``was_listening()`` depends on.

    Args:
        conn: Open connection, with a transaction already open.
        body: The validated §4.2 payload.
        station_id: The identity the bearer token authenticated as — not
            ``body.station_id``, which the caller has already checked matches.
    """
    provenance = find_station_provenance(conn, station_id)
    if provenance is None:  # pragma: no cover — the token just resolved to it
        raise MspError(NOT_OWNER, NOT_OWNER_MESSAGE)

    # `simulated` comes from the registration record, never from the wire: a
    # station's own claim about its nature is not evidence (D-048, CLAUDE.md
    # rule 5). MSP §4.2 puts no such field on the wire today, and this is what
    # keeps that true if a later revision adds one.
    insert_heartbeat(conn, body.to_new_heartbeat(simulated=provenance.simulated))
    touch_last_heartbeat(conn, station_id)


def _reported_holdings(body: HeartbeatRequestBody) -> HeldReport:
    """The station's own claims, lifted out of the §4.2 payload."""
    return HeldReport(
        held_assignment_ids=frozenset(body.held_assignments),
        listening_assignment_id=(
            body.listening.assignment_id if body.listening else None
        ),
    )


def _apply_reconciliation(
    conn: Connection, station_id: str, body: HeartbeatRequestBody
) -> None:
    """Move this station's assignments to match what it just reported.

    Args:
        conn: Open connection, with a transaction already open.
        station_id: The authenticated station.
        body: The validated §4.2 payload.

    Note:
        ``to_hold`` is applied before ``to_start`` because a station may confirm
        an assignment and report listening on it in the same heartbeat, and
        ``mark_assignment_in_progress`` moves a row only out of ``held``.

        Expiry runs last and is told what the station still names, so an
        assignment held past its window survives to be reported (D-067).
    """
    by_state = {
        state: find_assignment_ids_by_state(conn, station_id, [state])
        for state in LIVE_STATES
    }
    live = LiveAssignments(
        every_id=frozenset().union(*by_state.values()),
        awaiting_hold=frozenset(by_state["issued"]),
        already_held=frozenset(by_state["held"]),
    )
    outcome = reconcile(live, _reported_holdings(body))

    if outcome.foreign_ids:
        # MSP §4.2: "Present, but never issued to this station. Log and ignore;
        # do not act on it." Logged at warning because the only way to produce it
        # is a station implementation that invented an id or crossed its wires.
        _log.warning(
            "station %s reported holding %d assignment(s) never issued to it: %s",
            station_id,
            len(outcome.foreign_ids),
            ", ".join(outcome.foreign_ids),
        )

    if outcome.to_hold:
        mark_assignments_held(conn, station_id, outcome.to_hold)
    if outcome.to_start is not None:
        mark_assignment_in_progress(
            conn, station_id=station_id, assignment_id=outcome.to_start
        )
    expire_overdue_assignments(
        conn, station_id, still_held=sorted(body.held_assignments)
    )


def _assignments_due_now(
    conn: Connection, station_id: str, now: datetime
) -> list[DueAssignment]:
    """The assignments to return, capped at MSP §4.2's eight.

    Logs when a station's eligible set exceeds the cap. D-035 forbids that
    situation rather than paginating it — because held assignments are
    redelivered, the earliest 8 of 9 are the *same* 8 every time and the ninth
    may never be delivered at all. The warning is what makes a violation
    visible instead of silent; nothing enforces it yet, because nothing but a
    human creates assignments in Phase 1.
    """
    due = find_due_assignments(conn, station_id, horizon_end=now + ASSIGNMENT_HORIZON)
    if len(due) > MAX_ASSIGNMENTS_PER_RESPONSE:
        _log.warning(
            "station %s has %d eligible assignments, over MSP §4.2's cap of %d;"
            " the surplus will never be delivered (D-035)",
            station_id,
            len(due),
            MAX_ASSIGNMENTS_PER_RESPONSE,
        )
    return due[:MAX_ASSIGNMENTS_PER_RESPONSE]


@router.post("/heartbeat")
def heartbeat(
    body: HeartbeatRequestBody,
    station_id: str = Depends(get_authenticated_station_id),
    conn: Connection = Depends(get_connection),
) -> HeartbeatResponseBody:
    """Record a station's liveness and hand back the work it is due (MSP §4.2).

    **The token decides who the station is.** ``body.station_id`` is the
    station restating its own identity, and a disagreement between the two is
    refused rather than resolved in either direction — accepting the token's
    view would let a leaked credential file heartbeats under another station's
    name, and accepting the body's would be no authentication at all.

    Returns:
        ``{"assignments": [...], "server_time": ...}`` — MSP §4.2's response,
        with at most eight assignments.

    Raises:
        MspError: ``unauthorized`` (401) from the bearer dependency;
            ``not_owner`` (403) when the body names a different station.

    Note:
        Recording, reconciling and reading back happen in **one transaction**, so
        the response describes the state this heartbeat just produced. Split
        across two, a station could be told to hold an assignment that a
        concurrent write had already expired, and the listening evidence
        ``was_listening()`` rests on could land without the transition it
        justifies.
    """
    if body.station_id != station_id:
        _log.warning(
            "station %s sent a heartbeat claiming to be %s",
            station_id,
            body.station_id,
        )
        raise MspError(NOT_OWNER, NOT_OWNER_MESSAGE)

    now = platform_clock.utc_now()
    with conn.transaction():
        _record_heartbeat(conn, body, station_id)
        _apply_reconciliation(conn, station_id, body)
        due = _assignments_due_now(conn, station_id, now)

    return HeartbeatResponseBody(
        assignments=[AssignmentMessage.from_due_assignment(one) for one in due],
        server_time=platform_clock.format_server_time(now),
    )

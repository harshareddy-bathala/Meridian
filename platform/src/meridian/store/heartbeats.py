"""Heartbeats — the SQL layer behind MSP §4.2.

Reads and writes ``heartbeats`` and the one column of ``stations`` a
heartbeat touches (``deploy/migrations/sql/0006_heartbeats.sql``). Like the
rest of ``meridian.store``, this module makes no decision about what a
heartbeat *means* — reconciling ``held_assignments`` against
``meridian.store.assignments`` is a later session's orchestration, built on
top of this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian.store.stations import Connection

__all__ = [
    "ListeningReport",
    "NewHeartbeat",
    "insert_heartbeat",
    "touch_last_heartbeat",
]


@dataclass(frozen=True, slots=True)
class ListeningReport:
    """MSP §4.2's ``listening`` block.

    All four fields or none — there is no representation of "some fields
    present" in this type, matching the ``heartbeat_listening_complete``
    ``CHECK`` constraint one layer down. ``mode`` is carried, not dropped
    (D-028): a station tuned to the right frequency running the wrong
    demodulator did not observe the pass, and ``Registry.was_listening()``
    cannot tell that apart from a miss without it.
    """

    assignment_id: str
    satellite_id: str
    centre_freq_hz: int
    mode: str


@dataclass(frozen=True, slots=True)
class NewHeartbeat:
    """One MSP §4.2 heartbeat, in insertable form.

    Bundled rather than passed as separate parameters — past
    CLAUDE.local.md's cap of four (five, hard), the same reason
    ``store.stations.NewStation`` exists.
    """

    station_id: str
    sent_at: datetime
    state: str
    held_assignments: tuple[str, ...]
    listening: ListeningReport | None
    health_json: str
    simulated: bool
    clock_offset_s: float | None
    clock_uncertainty_s: float | None


def insert_heartbeat(conn: Connection, heartbeat: NewHeartbeat) -> None:
    """Insert one heartbeat row.

    ``received_at`` is left to the column's ``default now()`` — the
    platform's own clock, not a value this function is handed, the same
    reasoning ``store.stations.rotate_station_token`` applies to
    ``token_issued_at``.

    Does not touch ``stations.last_heartbeat_at``; see
    :func:`touch_last_heartbeat`.
    """
    listening = heartbeat.listening
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into heartbeats
                (station_id, sent_at, state, held_assignments,
                 listening_assignment_id, listening_satellite_id,
                 listening_freq_hz, listening_mode,
                 health_json, simulated, clock_offset_s, clock_uncertainty_s)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                heartbeat.station_id,
                heartbeat.sent_at,
                heartbeat.state,
                list(heartbeat.held_assignments),
                listening.assignment_id if listening else None,
                listening.satellite_id if listening else None,
                listening.centre_freq_hz if listening else None,
                listening.mode if listening else None,
                heartbeat.health_json,
                heartbeat.simulated,
                heartbeat.clock_offset_s,
                heartbeat.clock_uncertainty_s,
            ),
        )


def touch_last_heartbeat(conn: Connection, station_id: str) -> None:
    """Bump ``stations.last_heartbeat_at`` to ``now()``.

    Separate from :func:`insert_heartbeat` rather than folded into it, so a
    caller composing both under one transaction controls the ordering
    explicitly — matching how ``store.stations`` keeps ``insert_station``
    and ``rotate_station_token`` as separate calls a service composes.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update stations set last_heartbeat_at = now() where station_id = %s",
            (station_id,),
        )

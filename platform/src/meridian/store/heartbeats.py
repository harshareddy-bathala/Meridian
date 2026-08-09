"""Heartbeats — the SQL layer behind MSP §4.2.

Reads and writes ``heartbeats`` and the one column of ``stations`` a
heartbeat touches (``deploy/migrations/sql/0006_heartbeats.sql``). Like the
rest of ``meridian.store``, this module makes no decision about what a
heartbeat *means*. ``meridian.api.msp`` records each report through
:func:`insert_heartbeat` and :func:`touch_last_heartbeat`; reconciling a
report's ``held_assignments`` against ``meridian.store.assignments`` (MSP
§4.2) is not implemented yet, so nothing calls that module's transitions.

**Where ``NewHeartbeat.simulated`` comes from.** It is the station's own
provenance, read from its registration record via
``store.stations.find_station_provenance`` — not from the heartbeat payload,
which carries no such field (MSP §4.2). A station's claim about whether its own
data is simulated is not evidence, and simulated data appearing as measured is
the one error that would undermine every result this project reports. This
module takes whatever ``bool`` it is handed and cannot check it, which is why
the provenance of that value is written here rather than left to be inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import scalar_row

from meridian.store.stations import Connection

__all__ = [
    "ListeningEvidenceQuery",
    "ListeningReport",
    "NewHeartbeat",
    "has_listening_evidence",
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
    reasoning ``store.station_tokens.rotate_station_token`` applies to
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


@dataclass(frozen=True, slots=True)
class ListeningEvidenceQuery:
    """The resolved predicate behind ``Registry.was_listening()``.

    Every field is already a concrete bound. The frequency arrives as a range
    rather than a centre and a tolerance because deciding *how wide* the range
    should be is a domain judgement about Doppler shift, and this layer makes no
    domain judgements — ``registry.doppler_tolerance`` makes it and hands the
    result down (D-056).
    """

    station_id: str
    satellite_id: str
    mode: str
    freq_min_hz: int
    freq_max_hz: int
    window_start: datetime
    window_end: datetime


def has_listening_evidence(conn: Connection, query: ListeningEvidenceQuery) -> bool:
    """Whether any heartbeat confirms the station was listening as described.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        query: The resolved bounds to match against.

    Returns:
        True when at least one heartbeat matches every clause **and** names an
        assignment issued to that same station.

    Note:
        The join against ``assignments`` is not decoration. Without it a station
        could report a ``listening`` block quoting an ``assignment_id`` it
        invented, and the platform would accept that as proof it was working on
        a pass nobody scheduled — turning an absence into a confirmed miss on
        the station's own say-so. The join is on ``station_id`` as well as the
        id, so quoting *another* station's real assignment fails too.

        Matched on ``received_at``, the platform's clock and this hypertable's
        partitioning column. ``sent_at`` is the station's own and is untrusted
        (``0006_heartbeats.sql``); trusting it here would let a station with a
        broken clock assert coverage of any window it liked, and this predicate
        is the sole authority on what counts as a miss.

        The window is half-open — ``received_at >= start`` and ``< end`` — so
        two adjacent windows cannot both claim one heartbeat.
    """
    with conn.cursor(row_factory=scalar_row) as cur:
        cur.execute(
            """
            select exists (
                select 1
                from heartbeats as heartbeat
                join assignments as assignment
                  on assignment.assignment_id = heartbeat.listening_assignment_id
                 and assignment.station_id = heartbeat.station_id
                where heartbeat.station_id = %s
                  and heartbeat.listening_satellite_id = %s
                  and heartbeat.listening_mode = %s
                  and heartbeat.listening_freq_hz between %s and %s
                  and heartbeat.received_at >= %s
                  and heartbeat.received_at < %s
            )
            """,
            (
                query.station_id,
                query.satellite_id,
                query.mode,
                query.freq_min_hz,
                query.freq_max_hz,
                query.window_start,
                query.window_end,
            ),
        )
        return bool(cur.fetchone())

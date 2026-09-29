"""Taking an assignment back before its window, and giving it back — D-171.

Three transitions, each on a scheduled assignment whose window has not begun:

* **declined** — ``held -> revoked``, when the station stops naming it
  (D-003). The station let the work go, so its antenna's time is free, and the
  next round can give it to a pass that was skipped for this one;
* **offline** — ``issued|held -> revoked``, for a station ``offline`` when a
  round runs;
* **reinstated** — ``revoked -> held``, for an assignment the station still
  names, offline or declined. MSP has no message that takes work back from a
  station, and one that still holds an assignment will execute it; giving
  that window to anything else would ask one antenna for two receptions. So
  it is given back — while nothing newer claims the window: no later decision
  about the pass, and no live assignment of the station overlapping it. Where
  something does, the newer work stands, and the heartbeat logs the station
  as holding work it was told nothing about.

``issued`` that was never held and is absent is left alone, as in Phase 1: it
may not have arrived yet, and redelivery is how it does (D-026).

**Each transition is also an event in ``assignment_revocations``**, written by
the same statement (D-196). A reinstatement clears the assignment's
``revoked_reason``, which is right for its state and would otherwise erase the
only record that the platform ever took the work back.

Reference: docs/DECISIONS.md D-003, D-022, D-026, D-171, D-196;
docs/MSP-SPEC.md §4.2.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from meridian.store.stations import Connection

__all__ = ["reinstate_named", "revoke_declined", "revoke_offline"]


def revoke_declined(
    conn: Connection, station_id: str, *, still_held: Sequence[str], now: datetime
) -> int:
    """``held -> revoked`` for assignments ahead of their window the station dropped.

    Returns:
        How many were revoked.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            with changed as (
                update assignments
                set state = 'revoked', revoked_reason = 'declined', revoked_at = %s
                where station_id = %s
                  and decision = 'scheduled'
                  and state = 'held'
                  and start_at > %s
                  and assignment_id <> all(%s)
                returning assignment_id, station_id, revoked_at
            )
            insert into assignment_revocations
                (assignment_id, station_id, event, reason, at)
            select assignment_id, station_id, 'revoked', 'declined', revoked_at
            from changed
            """,
            (now, station_id, now, list(still_held)),
        )
        return cur.rowcount


def revoke_offline(conn: Connection, station_id: str, *, now: datetime) -> int:
    """``issued|held -> revoked`` for an offline station's work not yet begun.

    Returns:
        How many were revoked.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            with changed as (
                update assignments
                set state = 'revoked', revoked_reason = 'offline', revoked_at = %s
                where station_id = %s
                  and decision = 'scheduled'
                  and state in ('issued', 'held')
                  and start_at > %s
                returning assignment_id, station_id, revoked_at
            )
            insert into assignment_revocations
                (assignment_id, station_id, event, reason, at)
            select assignment_id, station_id, 'revoked', 'offline', revoked_at
            from changed
            """,
            (now, station_id, now),
        )
        return cur.rowcount


def reinstate_named(
    conn: Connection, station_id: str, *, named: Sequence[str], now: datetime
) -> int:
    """``revoked -> held`` for revoked assignments the station still holds.

    Offline or declined alike: a station naming an assignment will execute
    it, whatever it did before. Only while the window has not closed, and
    only where nothing newer claims it — no later decision about the pass,
    and no live assignment of the station whose window overlaps this one's.
    Either would mean the window has been given away, and the newer decision
    stands.

    Returns:
        How many were reinstated.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            with changed as (
                update assignments a
                set state = 'held', revoked_reason = null, revoked_at = null
                where a.station_id = %s
                  and a.state = 'revoked'
                  and a.end_at > %s
                  and a.assignment_id = any(%s)
                  and not exists (
                    select 1 from assignments later
                    where later.pass_id = a.pass_id
                      and later.model_config = a.model_config
                      and later.revision > a.revision
                  )
                  and not exists (
                    select 1 from assignments other
                    where other.station_id = a.station_id
                      and other.assignment_id <> a.assignment_id
                      and other.decision = 'scheduled'
                      and other.state in ('issued', 'held', 'in_progress')
                      and other.start_at < a.end_at
                      and a.start_at < other.end_at
                  )
                returning a.assignment_id, a.station_id
            )
            insert into assignment_revocations
                (assignment_id, station_id, event, reason, at)
            select assignment_id, station_id, 'reinstated', null, %s
            from changed
            """,
            (station_id, now, list(named), now),
        )
        return cur.rowcount

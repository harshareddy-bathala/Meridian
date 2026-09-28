"""Taking an assignment back before its window, and giving it back — D-171.

Three transitions, each on a scheduled assignment whose window has not begun:

* **declined** — ``held -> revoked``, when the station stops naming it
  (D-003). The station let the work go, so its antenna's time is free, and the
  next round can give it to a pass that was skipped for this one;
* **offline** — ``issued|held -> revoked``, for a station ``offline`` when a
  round runs;
* **reinstated** — ``revoked -> held``, for an offline station's assignment
  the station names again on its return. MSP has no message that takes work
  back from a station, and one that still holds an assignment will execute
  it; giving that window to anything else would ask one antenna for two
  receptions.

``issued`` that was never held and is absent is left alone, as in Phase 1: it
may not have arrived yet, and redelivery is how it does (D-026).

Reference: docs/DECISIONS.md D-003, D-022, D-026, D-171; docs/MSP-SPEC.md §4.2.
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
            update assignments
            set state = 'revoked', revoked_reason = 'declined', revoked_at = %s
            where station_id = %s
              and decision = 'scheduled'
              and state = 'held'
              and start_at > %s
              and assignment_id <> all(%s)
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
            update assignments
            set state = 'revoked', revoked_reason = 'offline', revoked_at = %s
            where station_id = %s
              and decision = 'scheduled'
              and state in ('issued', 'held')
              and start_at > %s
            """,
            (now, station_id, now),
        )
        return cur.rowcount


def reinstate_named(
    conn: Connection, station_id: str, *, named: Sequence[str], now: datetime
) -> int:
    """``revoked -> held`` for offline revocations the returning station still holds.

    Only while the window has not closed, and only where no later decision
    about the pass has been made: a station naming work that has since been
    decided again is stale, and the newer decision stands.

    Returns:
        How many were reinstated.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments a
            set state = 'held', revoked_reason = null, revoked_at = null
            where a.station_id = %s
              and a.state = 'revoked'
              and a.revoked_reason = 'offline'
              and a.end_at > %s
              and a.assignment_id = any(%s)
              and not exists (
                select 1 from assignments later
                where later.pass_id = a.pass_id
                  and later.model_config = a.model_config
                  and later.revision > a.revision
              )
            """,
            (station_id, now, list(named)),
        )
        return cur.rowcount

"""The periodic sweep D-067 owed: expire work nobody took, heartbeat or not.

``store.assignments.expire_overdue_assignments`` runs inside a station's own
heartbeat, so a station that stops heartbeating never has its overdue rows
swept (D-067). This sweep runs on a timer, for every station, and is narrower
than the heartbeat's on purpose:

* **Only ``issued`` rows.** The station never took them (D-008). A ``held`` or
  ``in_progress`` row is work the station took and may still be reporting from
  its queue; only its own heartbeat can say it let one go, so this sweep never
  touches one.
* **Only ``scheduled`` decisions.** A skip was never delivered (D-165).

Expiring a row late loses nothing: an observation that arrives for an
``expired`` assignment is still stored, it just does not move the state
(D-071), and classification reads the report first (D-181).

Reference: docs/DECISIONS.md D-008, D-067, D-071, D-183.
"""

from __future__ import annotations

from datetime import datetime

from meridian.store.stations import Connection

__all__ = ["expire_untaken_assignments"]


def expire_untaken_assignments(conn: Connection, *, now: datetime) -> int:
    """``issued -> expired`` for every scheduled row whose window closed untaken.

    Args:
        conn: An open connection. This function owns its transaction.
        now: The sweep's instant, timezone-aware UTC. Passed in, so a sweep can
            be stated exactly.

    Returns:
        How many rows expired.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments
            set state = 'expired'
            where decision = 'scheduled'
              and state = 'issued'
              and end_at < %s
            """,
            (now,),
        )
        return cur.rowcount

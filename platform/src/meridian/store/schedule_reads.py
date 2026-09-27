"""What a scheduler run needs to know about decisions already stored.

Reads ``assignments`` joined to ``passes``. Two questions, each asked once per
station per run:

* which of this horizon's passes this configuration has already decided, so a
  round re-running over an overlapping horizon does not decide them again; and
* which scheduled assignments the station is already committed to, so a pass
  new to this round — the horizon's tail, or a newer element set's prediction of
  a pass already taken (D-063) — cannot be scheduled on top of one.

Before D-165 neither was asked. The jobs service's rounds overlap (D-110), and a
newcomer that outranked a stored selection was inserted beside it: two
overlapping assignments for one antenna.

Reference: docs/DECISIONS.md D-066, D-110, D-165.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row, scalar_row

from meridian.store.stations import Connection

__all__ = ["StoredCommitment", "find_commitments", "find_decided_pass_ids"]


@dataclass(frozen=True, slots=True)
class StoredCommitment:
    """A scheduled assignment still open, with the pass it was made for."""

    assignment_id: str
    pass_id: int
    station_id: str
    aos: datetime
    los: datetime
    """The *pass* boundaries: conflicts are judged on the pass (D-065)."""

    max_elevation_deg: float
    priority: float
    simulated: bool


def find_decided_pass_ids(
    conn: Connection, pass_ids: Sequence[int], model_config: str
) -> set[int]:
    """The passes among ``pass_ids`` this configuration has a decision about.

    A skip counts as a decision: the round that made it recorded why, and the
    next round has no new reason to reverse it.
    """
    with conn.cursor(row_factory=scalar_row) as cur:
        cur.execute(
            """
            select pass_id from assignments
            where model_config = %s and pass_id = any(%s)
            """,
            (model_config, list(pass_ids)),
        )
        return set(cur.fetchall())


def find_commitments(
    conn: Connection, station_id: str, start: datetime, end: datetime
) -> list[StoredCommitment]:
    """The station's open scheduled assignments whose pass overlaps ``[start, end)``.

    Args:
        conn: An open connection. Read-only.
        station_id: The station whose antenna is being allocated.
        start: Inclusive lower bound on a pass's loss of signal.
        end: Exclusive upper bound on its acquisition.

    Returns:
        In acquisition order, then assignment id.

    Note:
        **Every configuration's commitments, not only the one being run.** A
        station has one antenna whichever configuration asked for the pass, and
        an assignment of A's is as binding on it as one of B's. Comparing
        configurations is done by replaying them over a snapshot (D-172), not by
        delivering two schedules to one station.

        **Open means ``issued``, ``held`` or ``in_progress``.** A reported or
        expired assignment is over and constrains nothing.
    """
    with conn.cursor(row_factory=class_row(StoredCommitment)) as cur:
        cur.execute(
            """
            select a.assignment_id, a.pass_id, a.station_id, p.aos, p.los,
                   p.max_elevation_deg, a.priority, a.simulated
            from assignments a
            join passes p on p.id = a.pass_id
            where a.station_id = %s
              and a.decision = 'scheduled'
              and a.state in ('issued', 'held', 'in_progress')
              and p.los >= %s
              and p.aos < %s
            order by p.aos asc, a.assignment_id asc
            """,
            (station_id, start, end),
        )
        return cur.fetchall()

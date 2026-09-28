"""What a scheduler run needs to know about decisions already stored.

Reads ``assignments`` joined to ``passes``. Two questions, each asked once per
station per run:

* what this configuration last decided about each of this horizon's passes,
  so a round decides again only what is open (D-165, D-171); and
* which scheduled assignments the station is already committed to, so a pass
  new to this round — the horizon's tail, or a newer element set's prediction of
  a pass already taken (D-063) — cannot be scheduled on top of one.

Before D-165 neither was asked. The jobs service's rounds overlap (D-110), and a
newcomer that outranked a stored selection was inserted beside it: two
overlapping assignments for one antenna.

Reference: docs/DECISIONS.md D-066, D-110, D-165, D-171.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "LatestDecision",
    "StoredCommitment",
    "find_commitments",
    "find_latest_decisions",
]


@dataclass(frozen=True, slots=True)
class StoredCommitment:
    """A scheduled assignment still open, with the pass it was made for."""

    assignment_id: str
    pass_id: int
    station_id: str
    aos: datetime
    los: datetime
    """The pass boundaries."""

    timing_uncertainty_s: float
    """The margin its window was widened by; conflicts are judged on the
    widened window (D-166)."""

    max_elevation_deg: float
    priority: float
    simulated: bool


@dataclass(frozen=True, slots=True)
class LatestDecision:
    """A configuration's current decision about one pass: its highest revision."""

    pass_id: int
    revision: int
    decision: str
    state: str
    revoked_reason: str | None
    conflicts_with_assignment_id: str | None


def find_latest_decisions(
    conn: Connection, pass_ids: Sequence[int], model_config: str
) -> dict[int, LatestDecision]:
    """This configuration's current decision about each of ``pass_ids`` it has decided.

    Which of them a round decides again is the caller's rule (D-171); this
    only says what each one's latest decision is.
    """
    with conn.cursor(row_factory=class_row(LatestDecision)) as cur:
        cur.execute(
            """
            select distinct on (pass_id)
                   pass_id, revision, decision, state, revoked_reason,
                   conflicts_with_assignment_id
            from assignments
            where model_config = %s and pass_id = any(%s)
            order by pass_id, revision desc
            """,
            (model_config, list(pass_ids)),
        )
        return {one.pass_id: one for one in cur.fetchall()}


def find_commitments(
    conn: Connection, station_id: str, start: datetime, end: datetime
) -> list[StoredCommitment]:
    """The station's open scheduled assignments whose window meets ``[start, end)``.

    Args:
        conn: An open connection. Read-only.
        station_id: The station whose antenna is being allocated.
        start: Inclusive lower bound on an assignment's ``end_at``.
        end: Exclusive upper bound on its ``start_at``.

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
                   a.timing_uncertainty_s, p.max_elevation_deg, a.priority,
                   a.simulated
            from assignments a
            join passes p on p.id = a.pass_id
            where a.station_id = %s
              and a.decision = 'scheduled'
              and a.state in ('issued', 'held', 'in_progress')
              and a.end_at >= %s
              and a.start_at < %s
            order by p.aos asc, a.assignment_id asc
            """,
            (station_id, start, end),
        )
        return cur.fetchall()

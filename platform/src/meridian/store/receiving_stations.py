"""The stations a pass can be computed for — where they are, and what they are.

Reads ``stations`` (``deploy/migrations/sql/0002_stations.sql``) and answers one
question: which stations can currently be given work, and at what coordinates.
``meridian.pass_generation`` and ``meridian.scheduler.run`` are its callers; both
turn a row from here into a ``meridian.orbit`` ground site.

**The coordinates it returns are the full precision the station registered
with.** A station may declare, under MSP §4.1, that its position be *published*
to fewer decimal places, and that setting is applied when a public response is
serialised and nowhere else (D-082). Rounding here instead would move a site by
up to 11 km, shifting every predicted acquisition by seconds and booking the
station for a place it is not. Nothing in this module reads that column.

Registration — writing a station row, recovering one, reading its provenance —
is ``meridian.store.stations``'. This module is separate because it serves a
different reader for a different purpose, and because that reader's one rule is
worth stating where it cannot be missed.

It makes no decision about *whether* a station should be scheduled; it only
retrieves rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["ReceivingStation", "find_receiving_stations"]


@dataclass(frozen=True, slots=True)
class ReceivingStation:
    """A station that passes can be computed for: where it is, and what it is.

    The location fields are what ``meridian.orbit`` needs to build a
    ``GroundSite``, and ``simulated`` is what a computed pass must inherit —
    a pass predicted for a simulated station is simulated data at every layer
    (D-013, CLAUDE.md rule 5). They are read together because a caller that
    fetched the location and forgot the provenance would write measured-looking
    rows for a virtual station.
    """

    station_id: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    simulated: bool


def find_receiving_stations(conn: Connection) -> list[ReceivingStation]:
    """Every station currently able to be given work.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.

    Returns:
        The matching stations in ascending ``station_id`` order, empty when
        there are none. Ordered rather than arbitrary because the
        pass-generation job walks this list, and a job whose output depends on
        the planner's row order is not reproducible.

    Note:
        **Excluded: deleted stations, and stations whose token was revoked.**
        Both are permanently unable to act — a revoked token cannot
        authenticate, so such a station can never be issued an assignment nor
        report against one (D-017, D-058). Computing its passes would add
        opportunities to EVALUATION.md §4.1's denominator that nothing could
        ever have taken, and every completeness figure would inherit the
        padding.

        **Not excluded: stations that are offline, stale or have never
        reported.** Liveness is a transient condition, and a station that was
        down for a day genuinely had passes it did not take — that is the
        outage, and it is what the reliability layer exists to measure. Removing
        those passes from the denominator would make an outage improve the
        numbers, which is the exact failure "absence is not a miss" guards
        against at the other end of the pipeline.
    """
    with conn.cursor(row_factory=class_row(ReceivingStation)) as cur:
        cur.execute(
            """
            select station_id, lat_deg, lon_deg, alt_m, simulated
            from stations
            where deleted_at is null
              and token_revoked_at is null
            order by station_id asc
            """
        )
        return cur.fetchall()

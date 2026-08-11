"""Reading back what a station declared it can receive.

Reads ``station_capabilities`` (``deploy/migrations/sql/0002_stations.sql``).
The write side lives in ``meridian.store.stations``, with the station row it
arrives alongside — a capability has no meaning without the station that
declared it, and both are inserted in one transaction at registration.

Reading is separate because it happens at a different time, for a different
reason, and needs different columns: registration writes everything a station
said, while scheduling asks only which hardware could receive a given downlink.
Keeping the read here also keeps ``stations.py`` inside the module-length limit
that ``station_tokens.py`` was split out to satisfy.

Storage only. Whether a declared range covers a particular transmitter is
``meridian.registry.capability_match``'s judgement, not this module's.

Reference: docs/MSP-SPEC.md §4.1; docs/DATA-MODEL.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "StoredCapability",
    "find_capabilities_for_station",
]


@dataclass(frozen=True, slots=True)
class StoredCapability:
    """One declared antenna and receiver chain, as scheduling needs it.

    Four of the nine columns. ``band`` is a label over the frequency range
    rather than a constraint of its own; ``polarisation``, ``tracking`` and
    ``horizon_mask_json`` bear on how well a pass is received rather than
    whether it can be attempted, and nothing decides anything with them yet.
    They stay in the table, unread, until something does — a narrower read is
    honest about what the scheduler currently uses.
    """

    freq_min_hz: int
    freq_max_hz: int
    modes: list[str]
    """Postgres ``text[]``, so psycopg hands back a list rather than a tuple."""

    min_elevation_deg: float


def find_capabilities_for_station(
    conn: Connection, station_id: str
) -> list[StoredCapability]:
    """Every live receiving chain one station declared.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The station to look up.

    Returns:
        Its capabilities in insertion order, empty when the station declared
        none, has had them all withdrawn, or does not exist. The three are
        deliberately one answer here: each means "there is no hardware to
        schedule against", and a caller that has to branch on which would be
        making a registration decision inside a scheduling loop.

    Note:
        Soft-deleted capabilities are excluded, matching the way a soft-deleted
        station reads as absent throughout ``store.stations``. An operator who
        withdraws an antenna has withdrawn it from scheduling; leaving it
        readable would keep generating passes for hardware that is gone.

        Ordered by ``id`` so a station with two chains is walked the same way on
        every run. The order does not change which passes are generated — the
        floor chosen is the lowest across matching chains, which is
        order-independent — but a reproducible job should not depend on that
        remaining true.
    """
    with conn.cursor(row_factory=class_row(StoredCapability)) as cur:
        cur.execute(
            """
            select freq_min_hz, freq_max_hz, modes, min_elevation_deg
            from station_capabilities
            where station_id = %s
              and deleted_at is null
            order by id asc
            """,
            (station_id,),
        )
        return cur.fetchall()

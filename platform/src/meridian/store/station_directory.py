"""The stations the public API lists, and the one it shows in detail.

Reads ``stations`` for the dashboard's directory: who is registered, where they
are, whether they are simulated, and when each last reported. It is a different
read from ``meridian.store.receiving_stations``' — that one answers "who can be
given work" for the scheduler, this one answers "what is there to look at" for a
reader — and the column lists differ accordingly.

**The projection is a list of columns, never ``select *``.** `stations` also
holds two token hashes, a registration key hash and a simulator seed, and this is
the module whose rows are published. Naming every column means a column added
later is invisible here until somebody decides it should not be, rather than
appearing on a public endpoint the day it lands.

Coordinates come back at full stored precision. The station's declared
publication precision travels beside them as a number, and the rounding is
applied when the response is serialised (D-082) — a store that rounded would be a
second place the rule lived, and this same row shape is what the detail endpoint
reads.

Liveness is **not** here. It is derived from ``last_heartbeat_at`` on read
against a single clock reading (D-054), which is the API layer's job: a list
classified row by row would measure each station against a slightly different
"now".

Pagination is keyset — the caller passes the last ``station_id`` it saw and gets
what sorts after it (D-085).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["DirectoryStation", "find_station", "find_stations_after"]

_DIRECTORY_COLUMNS = """
    station_id, name, operator,
    lat_deg, lon_deg, alt_m, location_precision_decimals,
    simulated, registered_at, last_heartbeat_at
"""
"""Every column both reads select, written once so the two cannot diverge.

A station's detail page and its row in the list must agree about the station; two
column lists maintained separately is how a field appears in one and not the
other, which reads to a user as data that is missing rather than as a projection
that drifted.
"""


@dataclass(frozen=True, slots=True)
class DirectoryStation:
    """One station as a reader may see it.

    Carries no credential and no seed. ``simulated`` is present rather than
    optional because every applicable public response states its provenance
    (CLAUDE.md rule 5), and a station is the record every pass, assignment and
    observation inherits that flag from.
    """

    station_id: str
    name: str
    operator: str

    lat_deg: float
    lon_deg: float
    alt_m: float
    """Full stored precision, all three. Rounded at serialisation (D-082)."""

    location_precision_decimals: int
    """How coarsely the operator permits the two coordinates above to be shown."""

    simulated: bool
    registered_at: datetime

    last_heartbeat_at: datetime | None
    """When this station last reported, or ``None`` if it never has.

    Returned raw so the caller can classify every station in a list against one
    clock reading. ``None`` is not "very old": a station that never reported is a
    commissioning problem, one that stopped is a fault, and
    ``registry.liveness.derive_liveness`` is what tells them apart.
    """


def find_stations_after(
    conn: Connection, station_id: str | None, limit: int
) -> list[DirectoryStation]:
    """One page of the station directory, ordered by id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The last id on the previous page, or ``None`` for the first
            page. Strictly exclusive, so no station is served twice.
        limit: The most rows to return. Callers ask for one more than the page
            needs and trim, which is how "is there another page" becomes a fact
            rather than a guess (D-085).

    Returns:
        The matching stations in ascending ``station_id`` order, empty when the
        cursor has passed the last one.

    Note:
        Soft-deleted stations are excluded, as everywhere else in the store. A
        revoked token is **not** excluded, unlike the scheduler's read: a station
        whose credential was withdrawn still existed and still has a history
        worth showing, and hiding it would make the directory disagree with every
        observation already attributed to it.
    """
    with conn.cursor(row_factory=class_row(DirectoryStation)) as cur:
        cur.execute(
            f"""
            select {_DIRECTORY_COLUMNS}
            from stations
            where deleted_at is null
              and (%s::text is null or station_id > %s)
            order by station_id asc
            limit %s
            """,
            (station_id, station_id, limit),
        )
        return cur.fetchall()


def find_station(conn: Connection, station_id: str) -> DirectoryStation | None:
    """One station by id, or ``None`` when there is no such station.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The station to look up.

    Returns:
        The station, or ``None`` — which a soft-deleted station also produces,
        so a caller cannot tell a retired station from one that never existed.
        That is deliberate: both mean "nothing to show here", and distinguishing
        them would publish the fact that an id was once in use.
    """
    with conn.cursor(row_factory=class_row(DirectoryStation)) as cur:
        cur.execute(
            f"""
            select {_DIRECTORY_COLUMNS}
            from stations
            where station_id = %s
              and deleted_at is null
            """,
            (station_id,),
        )
        return cur.fetchone()

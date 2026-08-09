"""The element-set archive — the SQL layer under orbit propagation.

Reads and writes ``element_sets`` (``deploy/migrations/sql/0003_satellites.sql``,
amended by ``0009_element_set_content_hash.sql``). Like the rest of
``meridian.store``, this module makes no decision about *which* element set a
prediction should use — it stores every set the platform is ever given and
retrieves them by satellite and time. Choosing one is the pass-generation job's
decision, and it is made through :func:`find_element_set_current_at`.

**The table is append-only: this module can insert and read, and that is all.**
A prediction's error can only be attributed to the age of the element set that
produced it if that set is still on disk, unmodified, years later — so the
archive keeps every set it is ever given, including several for one epoch when
a catalogue publishes a correction.

Storage only, no propagation: the sets read back here are handed to
``meridian.orbit`` to be turned into pass predictions.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-049, D-057.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "NewElementSet",
    "StoredElementSet",
    "find_element_set_current_at",
    "find_element_sets_in_epoch_range",
    "insert_element_set",
]


@dataclass(frozen=True, slots=True)
class NewElementSet:
    """One element set in insertable form.

    ``retrieved_at`` is absent on purpose — it is the platform's own clock and
    is left to the column default, the same reasoning
    ``store.heartbeats.insert_heartbeat`` applies to ``received_at``. ``epoch``
    *is* here because it is a property of the element set itself, read out of
    the lines rather than observed by us.

    ``content_sha256`` is absent for a different reason: it is a generated
    column, so the database computes it from the lines and no caller can supply
    a value that disagrees with them (D-057).
    """

    satellite_id: str
    epoch: datetime
    line1: str
    line2: str
    source: str
    """One of ``celestrak``, ``spacetrack``, ``manual``, ``simulator``.

    This table records provenance in ``source`` rather than a ``simulated``
    boolean — the one table in the schema that does, deliberately (D-049).
    """


@dataclass(frozen=True, slots=True)
class StoredElementSet:
    """One row of ``element_sets`` as read back.

    Carries ``id`` because ``passes.element_set_id`` references it: a stored
    pass names the exact set that produced it, which is what lets timing error
    be attributed to element-set age rather than to the satellite.
    """

    id: int
    satellite_id: str
    epoch: datetime
    retrieved_at: datetime
    line1: str
    line2: str
    source: str
    content_sha256: bytes


def insert_element_set(conn: Connection, element_set: NewElementSet) -> bool:
    """Archive one element set, or report that it was already held.

    Args:
        conn: An open connection.
        element_set: The set to store. Its content hash is computed by the
            database from the two lines.

    Returns:
        True when a row was written, False when this satellite already held
        these exact lines from this source.

    Note:
        **The return value carries the only signal that anything happened.**
        A duplicate is not an error here and raises nothing, so a caller
        counting retrievals, or deciding whether a new set justifies re-running
        a prediction, reads that from this boolean or not at all.

        "Already held" is judged on content, not on epoch (D-057). Two
        different sets sharing an epoch are two rows; the same set retrieved
        twice is one. Re-running an import is therefore free, which is what
        makes the archive safe to backfill.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into element_sets (satellite_id, epoch, line1, line2, source)
            values (%s, %s, %s, %s, %s)
            on conflict on constraint element_set_content_unique do nothing
            """,
            (
                element_set.satellite_id,
                element_set.epoch,
                element_set.line1,
                element_set.line2,
                element_set.source,
            ),
        )
        return cur.rowcount > 0


def find_element_set_current_at(
    conn: Connection, satellite_id: str, at: datetime
) -> StoredElementSet | None:
    """The newest element set for ``satellite_id`` whose epoch is not after ``at``.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        satellite_id: Catalogue identity, ``norad:NNNNN`` as text.
        at: The instant to be current at, timezone-aware UTC.

    Returns:
        The set that was current at ``at``, or None when the archive holds
        nothing for that satellite from before it.

    Note:
        **Current at an instant, never simply newest.** The distinction is
        invisible until the first backfill, and then it is the whole point of
        the archive: recomputing a pass from last March must use the set that
        was current last March, or the timing error measured against it is
        attributed to an element set that did not exist yet. Ordering by epoch
        rather than by ``retrieved_at`` says the same thing about the set's own
        validity rather than about when we happened to fetch it.

        Ties on epoch are broken by ``retrieved_at`` descending. Two sources can
        publish the same epoch (D-057 keeps both rows), and a query that decides
        this question differently on different days is not reproducible — which
        CLAUDE.md requires every number in a report to be.
    """
    with conn.cursor(row_factory=class_row(StoredElementSet)) as cur:
        cur.execute(
            """
            select id, satellite_id, epoch, retrieved_at,
                   line1, line2, source, content_sha256
            from element_sets
            where satellite_id = %s and epoch <= %s
            order by epoch desc, retrieved_at desc, id desc
            limit 1
            """,
            (satellite_id, at),
        )
        return cur.fetchone()


def find_element_sets_in_epoch_range(
    conn: Connection, satellite_id: str, start: datetime, end: datetime
) -> list[StoredElementSet]:
    """Every set for ``satellite_id`` with an epoch in ``[start, end)``.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        satellite_id: Catalogue identity, ``norad:NNNNN`` as text.
        start: Inclusive lower bound on epoch, timezone-aware UTC.
        end: Exclusive upper bound on epoch, timezone-aware UTC.

    Returns:
        The matching sets in ascending epoch order, empty when there are none.

    Note:
        Half-open, matching ``registry.was_listening``'s window convention, so
        two adjacent ranges cannot both claim one set.

        Ascending because the consumer is
        ``OrbitService.element_set_divergence``, which compares each set against
        the one before it; handing it a descending series would invert every
        sign it computes without changing any magnitude.
    """
    with conn.cursor(row_factory=class_row(StoredElementSet)) as cur:
        cur.execute(
            """
            select id, satellite_id, epoch, retrieved_at,
                   line1, line2, source, content_sha256
            from element_sets
            where satellite_id = %s and epoch >= %s and epoch < %s
            order by epoch asc, retrieved_at asc, id asc
            """,
            (satellite_id, start, end),
        )
        return cur.fetchall()

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
    "ARCHIVE_ONLY_SOURCES",
    "NewElementSet",
    "StoredElementSet",
    "find_current_element_sets",
    "find_current_epochs",
    "find_element_set_by_id",
    "find_element_set_current_at",
    "find_element_sets_in_epoch_range",
    "insert_element_set",
]


ARCHIVE_ONLY_SOURCES = ("satnogs_network",)
"""Sources whose sets are read only to compute an archive's denominator (D-325).

Another network's observation carries the element set it was scheduled with:
history no public catalogue still serves, and what lets Stage 16 compute the
passes an archive station could have heard on a day before our own fetches
began. It is never a runtime input (D-102, D-138), so every read here that
pass generation or the live surfaces use leaves these sources out. The raw
snapshot's archive selection, in ``store.snapshot_reads``, is the one reader
that takes them.
"""

_LIVE = "e.source <> all(%(archive_only)s)"
"""The filter every live read applies, written once."""


@dataclass(frozen=True, slots=True)
class NewElementSet:
    """One element set in insertable form.

    ``epoch`` is here because it is a property of the element set itself, read
    out of the lines rather than observed by us.

    ``content_sha256`` is absent: it is a generated column, so the database
    computes it from the lines and no caller can supply a value that disagrees
    with them (D-057).
    """

    satellite_id: str
    epoch: datetime
    line1: str
    line2: str
    source: str
    """One of ``celestrak``, ``spacetrack``, ``satnogs_network``, ``manual``,
    ``simulator``.

    This table records provenance in ``source`` rather than a ``simulated``
    boolean — the one table in the schema that does, deliberately (D-049).
    """

    ingest_record_id: int | None = None
    """The ingest artefact a fetched set was read from (D-320).

    None for a set loaded from a file, which has no artefact. The database
    refuses one on a ``manual`` or ``simulator`` set.
    """

    retrieved_at: datetime | None = None
    """When a fetched set was retrieved, from its artefact's manifest.

    None leaves the column default, the platform's own clock, which is what a
    file load means by it — the same reasoning ``store.heartbeats`` applies to
    ``received_at``. A set loaded from an artefact passes the artefact's own
    instant, because loading it hours after the fetch did not make it newer,
    and the tie-break in :func:`find_element_set_current_at` reads this.
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
            insert into element_sets (satellite_id, epoch, line1, line2, source,
                                      ingest_record_id, retrieved_at)
            values (%s, %s, %s, %s, %s, %s, coalesce(%s, now()))
            on conflict on constraint element_set_content_unique do nothing
            """,
            (
                element_set.satellite_id,
                element_set.epoch,
                element_set.line1,
                element_set.line2,
                element_set.source,
                element_set.ingest_record_id,
                element_set.retrieved_at,
            ),
        )
        return cur.rowcount > 0


@dataclass(frozen=True, slots=True)
class _CurrentEpoch:
    """One row of the current-epoch query, so the two columns arrive typed."""

    satellite_id: str
    epoch: datetime


def find_current_epochs(conn: Connection, at: datetime) -> dict[str, datetime]:
    """The epoch of the set current at ``at``, for every satellite tracked.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        at: The instant to be current at, timezone-aware UTC.

    Returns:
        One entry per satellite not deleted that holds a set from before ``at``.
        A satellite with none is absent, not dated: it has no age to report,
        and pass generation skips it for the same reason.

    Note:
        The epoch :func:`find_element_set_current_at` would choose, for every
        satellite at once, so the age a scrape reports is the age of the set
        the next round propagates (D-321).
    """
    with conn.cursor(row_factory=class_row(_CurrentEpoch)) as cur:
        cur.execute(
            f"""
            select e.satellite_id, max(e.epoch) as epoch
            from element_sets e
            join satellites s on s.satellite_id = e.satellite_id
            where s.deleted_at is null and e.epoch <= %(at)s and {_LIVE}
            group by e.satellite_id
            """,
            {"at": at, "archive_only": list(ARCHIVE_ONLY_SOURCES)},
        )
        return {row.satellite_id: row.epoch for row in cur.fetchall()}


def find_current_element_sets(
    conn: Connection, at: datetime
) -> dict[str, StoredElementSet]:
    """The set current at ``at``, for every satellite tracked, in one query.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        at: The instant to be current at, timezone-aware UTC.

    Returns:
        One entry per satellite not deleted that holds a set from before ``at``,
        keyed by satellite id. A satellite with none is absent.

    Note:
        The set :func:`find_element_set_current_at` would choose for each
        satellite, with the same order and tiebreaks, so a ground track on the
        dashboard comes from the set the next round of pass generation uses.
    """
    with conn.cursor(row_factory=class_row(StoredElementSet)) as cur:
        cur.execute(
            f"""
            select distinct on (e.satellite_id)
                   e.id, e.satellite_id, e.epoch, e.retrieved_at,
                   e.line1, e.line2, e.source, e.content_sha256
            from element_sets e
            join satellites s on s.satellite_id = e.satellite_id
            where s.deleted_at is null and e.epoch <= %(at)s and {_LIVE}
            order by e.satellite_id, e.epoch desc, e.retrieved_at desc, e.id desc
            """,
            {"at": at, "archive_only": list(ARCHIVE_ONLY_SOURCES)},
        )
        return {row.satellite_id: row for row in cur.fetchall()}


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
            f"""
            select e.id, e.satellite_id, e.epoch, e.retrieved_at,
                   e.line1, e.line2, e.source, e.content_sha256
            from element_sets e
            where e.satellite_id = %(satellite_id)s and e.epoch <= %(at)s
              and {_LIVE}
            order by e.epoch desc, e.retrieved_at desc, e.id desc
            limit 1
            """,
            {
                "satellite_id": satellite_id,
                "at": at,
                "archive_only": list(ARCHIVE_ONLY_SOURCES),
            },
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


def find_element_set_by_id(
    conn: Connection, element_set_id: int
) -> StoredElementSet | None:
    """The archived set with this id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        element_set_id: The id a stored pass recorded.

    Returns:
        The set, or None when no such row exists.

    Note:
        The lookup a *stored prediction* needs, as opposed to
        :func:`find_element_set_current_at`, which is how a new prediction
        chooses one. A pass names the exact set that produced it, so anything
        re-deriving that pass — the scheduler widening its window by the
        element set's age, the evaluation attributing timing error — reads it
        back by id rather than asking which set was current, and gets the same
        answer however much the archive has grown since.
    """
    with conn.cursor(row_factory=class_row(StoredElementSet)) as cur:
        cur.execute(
            """
            select id, satellite_id, epoch, retrieved_at,
                   line1, line2, source, content_sha256
            from element_sets
            where id = %s
            """,
            (element_set_id,),
        )
        return cur.fetchone()

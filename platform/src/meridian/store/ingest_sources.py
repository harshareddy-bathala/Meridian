"""The register of external sources — the SQL layer under archive ingest.

Reads and writes ``ingest_sources``
(``deploy/migrations/sql/0016_archive_ingest.sql``). One row per source we take
data from, carrying the licence and terms it publishes under and naming its
``ATTRIBUTION.md`` entry.

**The SQL for ingest lives here, not in the ingest distribution.** This
package's own rule is that only it calls the database, and a second dialect in a
second distribution would be a second set of conventions to keep aligned with
one schema. ``meridian_ingest`` depends on ``meridian`` and calls these
functions; nothing here knows that it exists (D-138).

**A source row is immutable.** There is no update function in this module and
that is the design: a change of terms is a new ``source_id``, so every stored
record keeps pointing at the terms it actually arrived under (D-140). Those
terms are what decide whether the evidence dataset may republish a record or
must reference it by checksum, which is a question asked long after the
retrieval and answerable only if nothing rewrote the answer.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-134, D-138, D-140.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "NewIngestSource",
    "StoredIngestSource",
    "find_active_ingest_sources",
    "find_ingest_source",
    "insert_ingest_source",
]


@dataclass(frozen=True, slots=True)
class NewIngestSource:
    """One source in insertable form.

    ``added_at`` is absent because it is the platform's own clock and is left to
    the column default, as ``NewElementSet.retrieved_at`` is. ``active`` is
    absent because a source is registered in order to be used: a row inserted
    inactive would be a record of an intention rather than of a source.
    """

    source_id: str
    """Ours, never the source's own name for itself.

    It is also a directory name in the raw store, so the column constrains it to
    ``^[a-z][a-z0-9_]{1,63}$``: no string from a remote source is ever a path
    element (D-141).
    """

    source_class: str
    """``archive_receptions``, ``space_weather``, ``atmospheric``, ``imagery``
    or ``regional_product``. A class rather than a vendor, as ``ATTRIBUTION.md``
    names them (D-132)."""

    name: str
    licence: str
    terms_url: str
    attribution_entry: str
    """Names the ``ATTRIBUTION.md`` entry this source was recorded under.

    So a record traces to the terms it arrived under rather than to whatever
    that file happens to say today.
    """

    access_constraint: str
    """``none``, ``key_counted`` or ``registration``."""


@dataclass(frozen=True, slots=True)
class StoredIngestSource:
    """One row of ``ingest_sources`` as read back."""

    source_id: str
    source_class: str
    name: str
    licence: str
    terms_url: str
    access_constraint: str
    attribution_entry: str
    added_at: datetime
    active: bool


def insert_ingest_source(conn: Connection, source: NewIngestSource) -> None:
    """Register one source.

    Args:
        conn: An open connection.
        source: The source to register, with its licence and terms.

    Raises:
        psycopg.errors.UniqueViolation: when ``source_id`` is already
            registered.
        psycopg.errors.CheckViolation: when the licence, terms URL or
            attribution entry is blank, or the source id is not a safe path
            element.

    Note:
        **A duplicate raises here rather than being swallowed**, which is the
        opposite of :func:`~meridian.store.element_sets.insert_element_set` and
        deliberate. That function judges "already held" on content, so a repeat
        is provably the same thing. This table has no content key: two rows with
        one ``source_id`` differ in their *terms*, and an ``on conflict do
        nothing`` would let a source whose licence changed go on collecting
        records under the licence it used to have — the precise failure D-140
        exists to prevent.

        So the question "does this already exist, and does it still say the same
        thing?" is answered explicitly by a caller reading
        :func:`find_ingest_source` first and comparing, rather than implicitly
        by a conflict clause that cannot compare.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into ingest_sources (source_id, source_class, name, licence,
                                        terms_url, access_constraint,
                                        attribution_entry)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source.source_id,
                source.source_class,
                source.name,
                source.licence,
                source.terms_url,
                source.access_constraint,
                source.attribution_entry,
            ),
        )


def find_ingest_source(conn: Connection, source_id: str) -> StoredIngestSource | None:
    """The registered source with this id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        source_id: The identifier we gave the source.

    Returns:
        The source, or None when nothing is registered under that id.

    Note:
        Read before every fetch, because a retrieval may not begin until the
        source's terms are recorded (D-134). The absence of a row is the refusal
        — the adapter stops rather than fetching and asking afterwards.
    """
    with conn.cursor(row_factory=class_row(StoredIngestSource)) as cur:
        cur.execute(
            """
            select source_id, source_class, name, licence, terms_url,
                   access_constraint, attribution_entry, added_at, active
            from ingest_sources
            where source_id = %s
            """,
            (source_id,),
        )
        return cur.fetchone()


def find_active_ingest_sources(conn: Connection) -> list[StoredIngestSource]:
    """Every source still in use, oldest first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.

    Returns:
        The active sources in registration order, empty when none is registered.

    Note:
        Retired sources are excluded rather than deleted. Records already taken
        under one stay readable and keep their provenance — a figure computed
        from them last month must still be explainable — but nothing new is
        fetched from it.

        Ordered by ``added_at`` then ``source_id`` so a listing is the same on
        two runs. CLAUDE.md requires every number in a report to be
        regenerable, and a command whose output order drifts makes a diff
        between two runs unreadable.
    """
    with conn.cursor(row_factory=class_row(StoredIngestSource)) as cur:
        cur.execute(
            """
            select source_id, source_class, name, licence, terms_url,
                   access_constraint, attribution_entry, added_at, active
            from ingest_sources
            where active
            order by added_at asc, source_id asc
            """
        )
        return cur.fetchall()

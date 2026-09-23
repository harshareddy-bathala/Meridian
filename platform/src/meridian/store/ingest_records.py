"""Retrieved artefacts — the SQL layer under the raw store.

Reads and writes ``ingest_records``
(``deploy/migrations/sql/0016_archive_ingest.sql``). One row per artefact we
retrieved, recording what it was, when we took it, what its bytes hash to and
where they sit in the raw store.

**Append-only.** A re-fetch that differs is a new row; the row it replaces is
marked, never rewritten. :func:`mark_ingest_record_superseded` fills a column
that was null and is the only update in this module — and, with the loader
above it, the only update in the whole ingest write path (D-141).

**Nothing here reads or writes a file.** The raw store is the ingest
distribution's, and this module holds only the relative path recorded for it:
keeping the filesystem out of the SQL layer is what lets the store be verified
against the database rather than by it.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-140, D-141.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "IngestRecordArrival",
    "NewIngestRecord",
    "StoredIngestRecord",
    "find_ingest_record_by_id",
    "find_ingest_records_for_source",
    "insert_ingest_record",
    "mark_ingest_record_superseded",
]


@dataclass(frozen=True, slots=True)
class NewIngestRecord:
    """One retrieved artefact in insertable form.

    ``retrieved_at`` is present, unlike the platform's own clock columns
    elsewhere in this package, because it is read from the manifest written
    beside the bytes when they arrived. The load may happen days later, and the
    column has no default precisely so that a fetch time can never be invented
    by whichever run happened to import it (D-141).
    """

    source_id: str
    original_identifier: str
    """The source's own identifier, verbatim. It is never a path element."""

    source_version: str
    """The source's own version of this artefact, usually a returned header.

    Non-empty, checked by the column: a retrieval that cannot say which version
    it took is refused before anything is published, because an artefact of
    unknown vintage cannot be compared against its successor.
    """

    payload_kind: str
    """``data`` or ``tile``. A ``tile`` may be displayed and referenced, and no
    query may derive a value from one (D-133)."""

    retrieved_at: datetime
    sha256: bytes
    """Of the raw bytes as downloaded, before any normalisation. Exactly 32."""

    raw_path: str
    """Where the artefact sits under the raw root, relative.

    Absolute paths and ``..`` segments are refused by the column. The writer
    already builds this from our own timestamp and our own checksum; the
    constraint is what stays true when somebody writes a second writer.
    """

    media_type: str
    byte_count: int
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    """The interval the artefact *describes* — not when we fetched it.

    A feature lookup selects on what an artefact describes and on when it was
    published, never on when we happened to take it (D-131).
    """

    spatial_extent: dict[str, float] | None = None
    """The ground it covers, as a bounding box, or None where it covers none."""


@dataclass(frozen=True, slots=True)
class StoredIngestRecord:
    """One row of ``ingest_records`` as read back."""

    record_id: int
    source_id: str
    original_identifier: str
    source_version: str
    payload_kind: str
    retrieved_at: datetime
    sha256: bytes
    raw_path: str
    media_type: str
    byte_count: int
    valid_from: datetime | None
    valid_to: datetime | None
    spatial_extent: dict[str, float] | None
    superseded_by: int | None
    """The record that replaced this one, or None while it is current."""


@dataclass(frozen=True, slots=True)
class _RecordId:
    """One ``record_id``, so the id comes back typed rather than cast.

    ``fetchone()`` under the default row factory yields a tuple of ``object``,
    and ``int(object)`` is not a valid call — the rest of this package reaches
    for ``str(row[0])``, which happens to typecheck for text keys and would
    quietly stringify an integer one here.
    """

    record_id: int


@dataclass(frozen=True, slots=True)
class IngestRecordArrival:
    """What happened when an artefact was offered to the table.

    ``record_id`` is always populated, so "it was already held, and here is the
    id it already has" is representable and "it was written, but I cannot tell
    you where" is not. That is the same shape
    ``meridian.observations.ingest`` uses for a replayed report, and for the
    same reason: a caller that has to ask a second question to find out what it
    just did will eventually forget to.
    """

    record_id: int
    written: bool


def insert_ingest_record(
    conn: Connection, record: NewIngestRecord
) -> IngestRecordArrival:
    """Record one retrieved artefact, or return the id it already has.

    Args:
        conn: An open connection.
        record: The artefact as its manifest describes it.

    Returns:
        The stored id, and whether this call was what wrote it.

    Note:
        **Identical is judged on content**, by ``(source_id,
        original_identifier, sha256)``: the same bytes fetched twice are one
        row, so re-running a fetch costs nothing and a backfill is safe to
        repeat. A *differing* re-fetch has a different digest and therefore
        inserts, which is what makes supersession visible instead of silent —
        see :func:`mark_ingest_record_superseded`.

        The second statement runs only on a conflict. ``on conflict do nothing``
        returns no row, so the existing id has to be read back; doing it inside
        the same transaction means the pair cannot interleave with another
        writer's insert.
    """
    with (
        conn.transaction(),
        conn.cursor(row_factory=class_row(_RecordId)) as cur,
    ):
        cur.execute(
            """
            insert into ingest_records (source_id, original_identifier,
                                        source_version, payload_kind,
                                        retrieved_at, sha256, raw_path,
                                        media_type, byte_count,
                                        valid_from, valid_to, spatial_extent)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            on conflict on constraint ingest_record_unique do nothing
            returning record_id
            """,
            (
                record.source_id,
                record.original_identifier,
                record.source_version,
                record.payload_kind,
                record.retrieved_at,
                record.sha256,
                record.raw_path,
                record.media_type,
                record.byte_count,
                record.valid_from,
                record.valid_to,
                None
                if record.spatial_extent is None
                else json.dumps(record.spatial_extent, sort_keys=True),
            ),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return IngestRecordArrival(record_id=inserted.record_id, written=True)

        cur.execute(
            """
            select record_id from ingest_records
            where source_id = %s and original_identifier = %s and sha256 = %s
            """,
            (record.source_id, record.original_identifier, record.sha256),
        )
        existing = cur.fetchone()
        if existing is None:  # pragma: no cover - the conflict proves the row
            message = "insert conflicted but the conflicting row is not readable"
            raise RuntimeError(message)
        return IngestRecordArrival(record_id=existing.record_id, written=False)


def mark_ingest_record_superseded(
    conn: Connection, record_id: int, superseded_by: int
) -> bool:
    """Record that a later retrieval replaced this artefact.

    Args:
        conn: An open connection.
        record_id: The older record, currently unsuperseded.
        superseded_by: The record that replaced it.

    Returns:
        True when the link was written, False when the row was already marked.

    Raises:
        psycopg.errors.CheckViolation: when a record is offered as its own
            successor.

    Note:
        **The only update in the ingest write path**, and it fills a column that
        was null rather than changing one that said something else. The
        ``where superseded_by is null`` clause is what makes that true under a
        second writer: a row already marked is left exactly as it was, and the
        False tells the caller so rather than a second link silently overwriting
        the first.

        Nothing is deleted and no bytes are touched. Both artefacts stay in the
        raw store, because a figure computed from the older one last month must
        still be explainable this month.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update ingest_records set superseded_by = %s
            where record_id = %s and superseded_by is null
            """,
            (superseded_by, record_id),
        )
        return cur.rowcount > 0


def find_ingest_record_by_id(
    conn: Connection, record_id: int
) -> StoredIngestRecord | None:
    """The stored artefact with this id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        record_id: The id a normalised row recorded.

    Returns:
        The record, or None when no such row exists.

    Note:
        The lookup a normalised row needs in order to name what it was computed
        from — an artefact with a checksum, a licence and a path — which is what
        makes a number on the dashboard traceable rather than asserted.
    """
    with conn.cursor(row_factory=class_row(StoredIngestRecord)) as cur:
        cur.execute(
            """
            select record_id, source_id, original_identifier, source_version,
                   payload_kind, retrieved_at, sha256, raw_path, media_type,
                   byte_count, valid_from, valid_to, spatial_extent,
                   superseded_by
            from ingest_records
            where record_id = %s
            """,
            (record_id,),
        )
        return cur.fetchone()


def find_ingest_records_for_source(
    conn: Connection, source_id: str
) -> list[StoredIngestRecord]:
    """Every artefact ever retrieved from this source, oldest first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        source_id: The source to list.

    Returns:
        The records in retrieval order, empty when none was ever taken.

    Returns superseded records too, and that is deliberate: their bytes are
    still in the raw store, ``verify`` has to check them, and a supersession
    that hid the artefact a published figure was computed from would make that
    figure unexplainable. Callers wanting only what is current filter on
    ``superseded_by is None``, which is a field on the row rather than a
    question for this module.

    Note:
        Ordered by ``retrieved_at`` then ``record_id``, so two runs list the
        same artefacts in the same order even when a batch shares a timestamp.
    """
    with conn.cursor(row_factory=class_row(StoredIngestRecord)) as cur:
        cur.execute(
            """
            select record_id, source_id, original_identifier, source_version,
                   payload_kind, retrieved_at, sha256, raw_path, media_type,
                   byte_count, valid_from, valid_to, spatial_extent,
                   superseded_by
            from ingest_records
            where source_id = %s
            order by retrieved_at asc, record_id asc
            """,
            (source_id,),
        )
        return cur.fetchall()

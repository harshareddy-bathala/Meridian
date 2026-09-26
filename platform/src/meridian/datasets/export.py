"""``meridian snapshot export`` — the one step of Stage 15 that reads the database.

Reads every table in :data:`~meridian.store.snapshot_reads.SNAPSHOT_TABLES`
inside one ``REPEATABLE READ, READ ONLY`` transaction, freezes the registry's
answer to "was this station listening" beside each assignment whose window has
closed, and publishes the lot as a sealed raw snapshot (D-143, D-144, D-145).

**``as_of`` is the transaction's time, never an argument.** Several columns are
current state rather than history, so a snapshot can only be of *now*; an
operator chooses where it starts (D-143).

**Listening is asked, not worked out.** :meth:`Registry.was_listening` is the
only authority on it (rule 7), and it needs the database, which the labeller
does not have. So it is called here, once per closed assignment, and its answer
is stored. The labeller reads the answer and nothing else, so the definition of
a miss stays in one place (D-145).

**Two steps: read inside the transaction, compute after it** (D-158).
:func:`read_snapshot` holds the ``REPEATABLE READ`` snapshot only while it
reads rows and asks the registry. :func:`export_snapshot` takes what was read —
no connection, so it cannot reach the database — and propagates:

* **each archive station's denominator** (D-150): its published location over
  the days it was active, frozen in ``archive_passes.jsonl``;
* **each measured pass's track** (D-158): azimuth and elevation every 30 s,
  frozen in ``pass_tracks.jsonl``.

So labelling and fitting count against files and never propagate, and however
long propagation takes as archive ingest grows, the database snapshot is not
held open for it. The rows are the same either way, because they were all read
at one instant.

**The registry is handed in.** This module depends on the ``Registry``
protocol, not on how a registry is built; the command builds one over the same
connection, so the listening answers are read inside the same snapshot as the
rows they describe.

Reference: docs/DECISIONS.md D-143, D-144, D-145, D-150, D-158.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from psycopg import IsolationLevel

from meridian.datasets.archive_passes import (
    ARCHIVE_PASSES,
    ArchiveRows,
    PassFinder,
    compute_archive_passes,
)
from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, SourceEntry, content_sha256, file_entry
from meridian.datasets.pass_tracks import (
    PASS_TRACKS,
    TrackFinder,
    TrackRows,
    compute_pass_tracks,
)
from meridian.datasets.publish import PublishedDirectory, publish_directory
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.registry import ListeningQuery, Registry
from meridian.store.schema_revision import find_current_revision
from meridian.store.snapshot_reads import (
    SNAPSHOT_TABLES,
    SnapshotScope,
    read_source_terms,
    read_table,
    snapshot_instant,
)
from meridian.store.stations import Connection

__all__ = [
    "LISTENING",
    "SIMULATED_SPLIT",
    "SNAPSHOTS",
    "Propagator",
    "SchemaMissingError",
    "SnapshotRead",
    "export_snapshot",
    "read_snapshot",
    "snapshot_transaction",
]

SNAPSHOTS = "snapshots"
"""Raw snapshots live under ``<datasets root>/snapshots/``."""

LISTENING = "listening"
"""The frozen ``was_listening`` answers, ``listening.jsonl``."""

SIMULATED_SPLIT = ("passes", "assignments", "observations", "heartbeats", "stations")
"""Tables counted in the manifest as measured and simulated, apart — never one
total across both populations (rule 5)."""


class SchemaMissingError(RuntimeError):
    """The database records no migration, so no snapshot could say what it read."""


class Propagator(PassFinder, TrackFinder, Protocol):
    """What the export propagates with: pass windows and look angles."""


@dataclass(frozen=True, slots=True)
class SnapshotRead:
    """Everything read inside one transaction, and nothing computed from it."""

    schema_revision: str
    scope: SnapshotScope
    tables: Mapping[str, Sequence[Mapping[str, object]]]
    listening: Sequence[Mapping[str, object]]
    sources: tuple[SourceEntry, ...]


@dataclass(frozen=True, slots=True)
class _Computed:
    """What export propagates from the rows, after the transaction."""

    archive_passes: Sequence[Mapping[str, object]]
    pass_tracks: Sequence[Mapping[str, object]]
    counts: Mapping[str, int]


@contextmanager
def snapshot_transaction(conn: Connection) -> Iterator[Connection]:
    """One ``REPEATABLE READ, READ ONLY`` transaction for a whole export.

    Args:
        conn: A connection with no transaction open. Its isolation level and
            read-only flag apply at ``BEGIN``, so they cannot be changed inside
            a transaction that has already started.

    Yields:
        The same connection, inside the transaction.

    Note:
        Repeatable read is what makes every table be read at one instant — the
        instant ``now()`` returns — rather than each at the moment its own
        query ran. Read only means an export that tried to write anything,
        by mistake, fails instead of changing the database it is describing.
    """
    previous = (conn.isolation_level, conn.read_only)
    conn.isolation_level = IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.isolation_level, conn.read_only = previous


def read_snapshot(
    conn: Connection, registry: Registry, *, since: datetime
) -> SnapshotRead:
    """Read every table and ask about every closed assignment, once.

    Args:
        conn: A connection inside :func:`snapshot_transaction` — or, in a
            test, inside any transaction.
        registry: Answers ``was_listening`` over the same connection.
        since: Where the snapshot starts. It ends at the transaction's time.

    Returns:
        The rows, the listening answers and the sources' terms. Nothing here
        needs the connection afterwards, so the transaction can end.

    Raises:
        SchemaMissingError: The database has no migration recorded.
        ValueError: ``since`` is after the transaction's time, or naive.
    """
    schema = find_current_revision(conn)
    if schema is None:
        message = "the database records no migration; run `meridian db upgrade`"
        raise SchemaMissingError(message)
    scope = SnapshotScope(since=since, as_of=snapshot_instant(conn))
    tables = {one.name: read_table(conn, one, scope) for one in SNAPSHOT_TABLES}
    satellite_of = {one["id"]: one["satellite_id"] for one in tables["passes"]}
    return SnapshotRead(
        schema_revision=schema,
        scope=scope,
        tables=tables,
        listening=[
            _listening(registry, one, satellite_of[one["pass_id"]])
            for one in tables["assignments"]
            if one["decision"] == "scheduled" and _closed(one, scope.as_of)
        ],
        sources=tuple(
            SourceEntry(
                source_id=one.source_id,
                licence=one.licence,
                terms_url=one.terms_url,
                attribution_entry=one.attribution_entry,
                records=one.records,
            )
            for one in read_source_terms(conn, scope)
        ),
    )


def export_snapshot(
    read: SnapshotRead,
    *,
    root: Path,
    created_at: datetime,
    orbit: Propagator | None = None,
) -> PublishedDirectory:
    """Propagate what was read, and publish it all as a raw snapshot.

    Takes no connection: whatever this does, and however long it takes, the
    database snapshot it describes is already closed (D-158).

    Args:
        read: What :func:`read_snapshot` read.
        root: The datasets root; the snapshot goes under ``root/snapshots``.
        created_at: When this run happened, recorded and never hashed.
        orbit: The orbit service to propagate with; ours unless a test hands
            in another.

    Returns:
        Where the snapshot landed and its manifest.
    """
    computed = _compute(read, SkyfieldOrbitService() if orbit is None else orbit)
    files = {
        f"{name}.jsonl": _jsonl(table)
        for name, table in (
            *read.tables.items(),
            (LISTENING, read.listening),
            (ARCHIVE_PASSES, computed.archive_passes),
            (PASS_TRACKS, computed.pass_tracks),
        )
    }
    manifest = Manifest(
        kind="raw_snapshot",
        schema_revision=read.schema_revision,
        since=read.scope.since,
        as_of=read.scope.as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=created_at,
        counts=_counts(read) | dict(computed.counts),
        sources=read.sources,
    )
    stamp = read.scope.as_of.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{content_sha256(manifest).hex()[:12]}"
    return publish_directory(root / SNAPSHOTS, name, manifest, files)


def _compute(read: SnapshotRead, orbit: Propagator) -> _Computed:
    """The archive stations' passes and our passes' tracks."""
    tables = read.tables
    archive = compute_archive_passes(
        ArchiveRows(
            stations=tables["archive_stations"],
            receptions=tables["archive_observations"],
            element_sets=tables["element_sets"],
        ),
        since=read.scope.since,
        as_of=read.scope.as_of,
        orbit=orbit,
    )
    tracks = compute_pass_tracks(
        TrackRows(
            passes=tables["passes"],
            stations=tables["stations"],
            element_sets=tables["element_sets"],
        ),
        orbit,
    )
    return _Computed(
        archive_passes=archive.rows,
        pass_tracks=tracks.rows,
        counts={"archive_passes": len(archive.rows)}
        | dict(archive.counts)
        | dict(tracks.counts),
    )


def _closed(assignment: Mapping[str, object], as_of: datetime) -> bool:
    """Whether an assignment's window had ended by the snapshot's instant."""
    end_at = assignment["end_at"]
    return isinstance(end_at, datetime) and end_at <= as_of


def _listening(
    registry: Registry, assignment: Mapping[str, object], satellite_id: object
) -> Mapping[str, object]:
    """Ask the registry about one assignment; keep the question with the answer."""
    query = ListeningQuery(
        station_id=str(assignment["station_id"]),
        satellite_id=str(satellite_id),
        centre_freq_hz=_integer(assignment["centre_freq_hz"]),
        mode=str(assignment["mode"]),
        window=(_instant(assignment["start_at"]), _instant(assignment["end_at"])),
    )
    return {
        "assignment_id": assignment["assignment_id"],
        "station_id": query.station_id,
        "satellite_id": query.satellite_id,
        "centre_freq_hz": query.centre_freq_hz,
        "mode": query.mode,
        "window_start": query.window[0],
        "window_end": query.window[1],
        "listening_confirmed": registry.was_listening(query),
    }


def _counts(read: SnapshotRead) -> dict[str, int]:
    """Measured and simulated apart for every table that can hold both."""
    counts: dict[str, int] = {}
    for name in SIMULATED_SPLIT:
        table = read.tables[name]
        simulated = sum(1 for one in table if one["simulated"] is True)
        counts[f"{name}.measured"] = len(table) - simulated
        counts[f"{name}.simulated"] = simulated
    confirmed = sum(1 for one in read.listening if one["listening_confirmed"])
    counts["listening.confirmed"] = confirmed
    counts["listening.not_confirmed"] = len(read.listening) - confirmed
    return counts


def _jsonl(rows: Sequence[Mapping[str, object]]) -> bytes:
    """Rows as one JSON Lines file, in the order they were read."""
    return b"".join(canonical_line(one) for one in rows)


def _instant(value: object) -> datetime:
    """A timestamp column's value, which psycopg hands back as a datetime."""
    if not isinstance(value, datetime):
        message = f"expected a timestamp, found {type(value).__name__}"
        raise TypeError(message)
    return value


def _integer(value: object) -> int:
    """A ``bigint`` column's value."""
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"expected an integer, found {type(value).__name__}"
        raise TypeError(message)
    return value

"""Putting a normalised artefact into the archive tables, one artefact at a time.

Everything before this module is offline and reversible: bytes in a directory,
records in memory. This is where an artefact becomes rows, and the only place
in the distribution that touches a database — through ``meridian.store``, never
with SQL of its own, because ``store/__init__.py`` says that package is the one
that talks to Postgres and a second dialect here would be a second set of
conventions to keep aligned with one schema.

**One transaction per artefact.** A half-loaded artefact is not a smaller
artefact: it is a month of receptions with some of its stations missing, which
every later count reads as a fact about the archive. So the record, its
stations and its receptions land together or not at all, and a run that died
halfway is resumed by running it again.

**Running it again is the normal case, not the recovery case.** Every insert
below is content-keyed, so a second load of the same tree writes nothing and
says so. That is what lets the completion gate be demonstrated by simply doing
it twice.

**A tile is skipped and counted, never normalised.** D-133: a tile is a picture
of a measurement, and the count is how "we hold tiles we derived nothing from"
stays visible rather than becoming a gap.

Reference: docs/DECISIONS.md D-133, D-139, D-140, D-141.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian.store.archive_observations import (
    NewArchiveObservation,
    insert_archive_observation,
)
from meridian.store.archive_stations import NewArchiveStation, insert_archive_station
from meridian.store.ingest_records import (
    NewIngestRecord,
    find_ingest_records_for_source,
    insert_ingest_record,
    mark_ingest_record_superseded,
)
from meridian.store.ingest_sources import (
    NewIngestSource,
    find_ingest_source,
    insert_ingest_source,
)
from meridian.store.stations import Connection
from meridian_ingest.adapters import adapter_for, normaliser_for
from meridian_ingest.adapters.protocol import Normaliser, SourceDescriptor
from meridian_ingest.load_report import ArtefactLoad, LoadReport
from meridian_ingest.normalise.records import NormalisedBatch
from meridian_ingest.raw_manifest import RawManifest
from meridian_ingest.raw_store import RawStore

__all__ = [
    "TermsChangedError",
    "load_artefact",
    "load_source",
    "register_source",
]

PERMISSION_FIELDS = (
    "source_class",
    "licence",
    "terms_url",
    "attribution_entry",
    "access_constraint",
)
"""The fields that decide what may be done with a source's data.

``name`` is deliberately absent. It is a label for a human and grants nothing,
so it is allowed to drift rather than forcing a new ``source_id`` over a
corrected spelling — but every field here changing means the terms changed, and
``ingest_sources`` is insert-only precisely so stored records keep pointing at
the terms they actually arrived under (D-140).
"""


class TermsChangedError(RuntimeError):
    """An adapter now declares terms different from the ones already stored.

    Refused rather than updated. Editing the row would silently restate the
    terms every record already stored arrived under, and those terms decide
    whether the evidence dataset may republish a record or must reference it by
    checksum (D-104, D-136). A change of terms is a new ``source_id``.
    """


def register_source(conn: Connection, descriptor: SourceDescriptor) -> bool:
    """Record a source, or confirm that the stored terms still match.

    Args:
        conn: An open connection.
        descriptor: What the adapter declares about the source.

    Returns:
        True when this call wrote the row, False when it was already there.

    Raises:
        TermsChangedError: The stored row and the adapter disagree about
            something in :data:`PERMISSION_FIELDS`.

    Note:
        The check is the point. ``ingest_sources`` is insert-only, so without
        it an adapter whose licence had changed would keep loading against a
        row asserting the old one, and every record retrieved afterwards would
        cite terms nobody agreed to.
    """
    stored = find_ingest_source(conn, descriptor.source_id)
    if stored is None:
        insert_ingest_source(conn, _new_source(descriptor))
        return True
    differing = [
        f"{name}: stored {getattr(stored, name)!r}, adapter says "
        f"{getattr(descriptor, name)!r}"
        for name in PERMISSION_FIELDS
        if getattr(stored, name) != getattr(descriptor, name)
    ]
    if differing:
        joined = "; ".join(differing)
        message = (
            f"{descriptor.source_id} is already registered under different terms — "
            f"{joined}. A change of terms is a new source_id (D-140), so stored "
            "records keep pointing at what was actually agreed to"
        )
        raise TermsChangedError(message)
    return False


def load_source(conn: Connection, store: RawStore, source_id: str) -> LoadReport:
    """Load every artefact held for one source.

    Args:
        conn: An open connection.
        store: The raw store holding the retrieved artefacts.
        source_id: A registered source.

    Returns:
        What each artefact did.

    Raises:
        TermsChangedError: The source's terms no longer match the stored row.
        UnknownSourceError: Nothing is registered under that id.

    Note:
        Artefacts load in retrieval order, which is the order the raw store
        lists them in, so a second run visits them identically and a
        supersession is recorded in the direction it actually happened.

        **Each artefact commits on its own only on an autocommit connection.**
        On one already inside a transaction, every artefact's block is a
        savepoint, and nothing is kept until the caller commits. That is what
        the tests want, and why ``meridian-ingest load`` sets autocommit.
    """
    registered = register_source(conn, adapter_for(source_id).descriptor)
    normaliser = normaliser_for(source_id)
    loads = tuple(
        load_artefact(conn, store, normaliser, raw_path)
        for raw_path in store.scan(source_id)
    )
    return LoadReport(
        source_id=source_id, source_registered=registered, artefacts=loads
    )


def load_artefact(
    conn: Connection, store: RawStore, normaliser: Normaliser, raw_path: str
) -> ArtefactLoad:
    """Load one artefact, atomically.

    Args:
        conn: An open connection.
        store: The raw store to read the artefact from.
        normaliser: The normaliser registered for its source.
        raw_path: Which artefact, as ``ingest_records.raw_path`` holds it.

    Returns:
        What this artefact's load did.

    Raises:
        NormalisationError: The bytes do not describe rows this schema can hold.
        NormalisationDisagreementError: A reception is already stored under this
            transformation version with a different body. The whole artefact
            rolls back, because a normaliser that is not deterministic is a bug
            in the one property this stage exists to demonstrate.

    Note:
        **Always normalises**, even when the artefact was already recorded. A
        run that died after inserting the record would otherwise leave an
        artefact nothing was ever derived from, and a normaliser whose version
        has been bumped would never be applied to what is already held.
    """
    stored = store.read(raw_path)
    manifest = stored.manifest
    with conn.transaction():
        arrival = insert_ingest_record(conn, _new_record(manifest, raw_path))
        superseded = (
            _supersede(conn, manifest, arrival.record_id) if arrival.written else ()
        )
        if manifest.provenance.payload_kind == "tile":
            return ArtefactLoad(
                raw_path=raw_path,
                record_id=arrival.record_id,
                record_written=arrival.written,
                superseded=superseded,
                skipped="tile",
            )
        batch = normaliser.normalise(stored)
        stations = _load_stations(conn, manifest, arrival.record_id, batch)
        receptions = _load_receptions(
            conn, manifest, arrival.record_id, batch, stations
        )
    return ArtefactLoad(
        raw_path=raw_path,
        record_id=arrival.record_id,
        record_written=arrival.written,
        superseded=superseded,
        stations_written=stations.written,
        stations_already_held=stations.already_held,
        receptions_written=receptions.written,
        receptions_already_held=receptions.already_held,
    )


@dataclass(frozen=True, slots=True)
class _Applied:
    """How many rows an insert loop wrote, and how many it found already there."""

    written: int = 0
    already_held: int = 0
    by_key: dict[str, int] = field(default_factory=dict)


def _load_stations(
    conn: Connection, manifest: RawManifest, record_id: int, batch: NormalisedBatch
) -> _Applied:
    """Store this artefact's station descriptions, keeping their ids by key."""
    applied = _Applied()
    for station in batch.stations:
        arrival = insert_archive_station(
            conn,
            NewArchiveStation(
                record_id=record_id,
                source_id=manifest.provenance.source_id,
                source_station_key=station.source_station_key,
                content_sha256=station.content_sha256,
                name=station.name,
                lat_deg=station.lat_deg,
                lon_deg=station.lon_deg,
                alt_m=station.alt_m,
                capability=None
                if station.capability is None
                else dict(station.capability),
            ),
        )
        applied.by_key[station.source_station_key] = arrival.archive_station_id
        applied = _counted(applied, arrival.written)
    return applied


def _load_receptions(
    conn: Connection,
    manifest: RawManifest,
    record_id: int,
    batch: NormalisedBatch,
    stations: _Applied,
) -> _Applied:
    """Store this artefact's receptions, linked to the stations just stored."""
    applied = _Applied()
    for reception in batch.receptions:
        arrival = insert_archive_observation(
            conn,
            NewArchiveObservation(
                record_id=record_id,
                source_id=manifest.provenance.source_id,
                source_observation_id=reception.source_observation_id,
                transformation_version=batch.transformation_version,
                content_sha256=reception.content_sha256,
                archive_station_id=_station_id(reception.source_station_key, stations),
                satellite_key=reception.satellite_key,
                satellite_key_kind=reception.satellite_key_kind,
                started_at=reception.started_at,
                ended_at=reception.ended_at,
                max_elevation_deg=reception.max_elevation_deg,
                centre_freq_hz=reception.centre_freq_hz,
                mode=reception.mode,
                archive_outcome=reception.archive_outcome,
                source_outcome=reception.source_outcome,
                peak_snr_db=reception.peak_snr_db,
                frames_decoded=reception.frames_decoded,
            ),
        )
        applied = _counted(applied, arrival.written)
    return applied


def _station_id(key: str | None, stations: _Applied) -> int | None:
    """The id of a station this same artefact described, or None where it named none.

    A key that is not in the batch cannot reach here —
    :class:`NormalisedBatch` refuses one — so a miss is a bug rather than an
    archive's omission, and it stops the load rather than quietly storing a
    reception with no station.
    """
    if key is None:
        return None
    try:
        return stations.by_key[key]
    except KeyError as exc:  # pragma: no cover — the batch check makes this dead
        message = f"reception names station {key!r}, which this artefact did not store"
        raise RuntimeError(message) from exc


def _counted(applied: _Applied, written: bool) -> _Applied:
    """One more row accounted for, on whichever side it fell."""
    return _Applied(
        written=applied.written + (1 if written else 0),
        already_held=applied.already_held + (0 if written else 1),
        by_key=applied.by_key,
    )


def _supersede(
    conn: Connection, manifest: RawManifest, record_id: int
) -> tuple[int, ...]:
    """Link any earlier unsuperseded record for this identifier to the new one.

    A differing re-fetch has a different digest, so it inserts rather than
    conflicting — which is what makes supersession visible instead of silent.

    Reads the source's whole history and filters here rather than asking for
    the one row: at this stage a source holds tens of artefacts, and the
    narrower query (``where original_identifier = %s and superseded_by is
    null``) belongs in ``meridian.store`` when one holds thousands.
    """
    identifier = manifest.provenance.original_identifier
    earlier = [
        one.record_id
        for one in find_ingest_records_for_source(conn, manifest.provenance.source_id)
        if one.original_identifier == identifier
        and one.superseded_by is None
        and one.record_id != record_id
    ]
    return tuple(
        one for one in earlier if mark_ingest_record_superseded(conn, one, record_id)
    )


def _new_source(descriptor: SourceDescriptor) -> NewIngestSource:
    """The descriptor in insertable form."""
    return NewIngestSource(
        source_id=descriptor.source_id,
        source_class=descriptor.source_class,
        name=descriptor.name,
        licence=descriptor.licence,
        terms_url=descriptor.terms_url,
        attribution_entry=descriptor.attribution_entry,
        access_constraint=descriptor.access_constraint,
    )


def _new_record(manifest: RawManifest, raw_path: str) -> NewIngestRecord:
    """The manifest in insertable form.

    Every value comes from the manifest written beside the bytes, never from
    this run's clock or its idea of what it fetched. A load happening days
    after the retrieval records the retrieval, not the load (D-141).
    """
    origin = manifest.provenance
    return NewIngestRecord(
        source_id=origin.source_id,
        original_identifier=origin.original_identifier,
        source_version=origin.source_version,
        payload_kind=origin.payload_kind,
        retrieved_at=origin.retrieved_at,
        sha256=manifest.sha256,
        raw_path=raw_path,
        media_type=origin.media_type,
        byte_count=manifest.byte_count,
        valid_from=origin.valid_from,
        valid_to=origin.valid_to,
        spatial_extent=origin.spatial_extent,
    )

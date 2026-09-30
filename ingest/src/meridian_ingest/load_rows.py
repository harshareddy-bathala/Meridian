"""Rows in insertable form, and the loop that stores an artefact's values.

Split from :mod:`meridian_ingest.load` so the loader reads as the sequence it
is — record, supersede, normalise, store — while the column-by-column copying
sits here. Every value comes from the manifest or the normalised batch, never
from this run's clock (D-141).

Reference: docs/DECISIONS.md D-140, D-141, D-221.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.store.environment_samples import (
    NewEnvironmentSample,
    insert_environment_sample,
)
from meridian.store.ingest_records import NewIngestRecord
from meridian.store.ingest_sources import NewIngestSource
from meridian.store.stations import Connection
from meridian_ingest.adapters.protocol import SourceDescriptor
from meridian_ingest.normalise.records import NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample
from meridian_ingest.raw_manifest import RawManifest

__all__ = ["SamplesApplied", "load_samples", "new_record", "new_source"]


@dataclass(frozen=True, slots=True)
class SamplesApplied:
    """How many values one artefact wrote, and how many were already held."""

    written: int = 0
    already_held: int = 0


def load_samples(
    conn: Connection, manifest: RawManifest, record_id: int, batch: NormalisedBatch
) -> SamplesApplied:
    """Store every published value one artefact normalised to.

    Args:
        conn: An open connection, inside the artefact's transaction.
        manifest: The artefact's manifest, for its source.
        record_id: The artefact's row.
        batch: What it normalised to.

    Returns:
        The counts, written and already held apart.

    Raises:
        NormalisationDisagreementError: A value is already stored under this
            key and version with a different body; the artefact rolls back.
    """
    written = 0
    for sample in batch.samples:
        arrival = insert_environment_sample(
            conn,
            _new_sample(
                manifest.provenance.source_id,
                record_id,
                batch.transformation_version,
                sample,
            ),
        )
        written += 1 if arrival.written else 0
    return SamplesApplied(written=written, already_held=len(batch.samples) - written)


def _new_sample(
    source_id: str, record_id: int, version: str, sample: NormalisedSample
) -> NewEnvironmentSample:
    return NewEnvironmentSample(
        record_id=record_id,
        source_id=source_id,
        transformation_version=version,
        series_key=sample.series_key,
        content_sha256=sample.content_sha256,
        quantity=sample.quantity,
        value_unit=sample.value_unit,
        observed_from=sample.observed_from,
        observed_to=sample.observed_to,
        published_at=sample.published_at,
        published_basis=sample.published_basis,
        product=sample.product,
        value=sample.value,
        missing_reason=sample.missing_reason,
        lat_deg=sample.lat_deg,
        lon_deg=sample.lon_deg,
        footprint_m=sample.footprint_m,
        quality=sample.quality,
    )


def new_source(descriptor: SourceDescriptor) -> NewIngestSource:
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


def new_record(manifest: RawManifest, raw_path: str) -> NewIngestRecord:
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

"""Values public products published — indices, conditions, composites, detections.

Reads and writes ``environment_samples``
(``deploy/migrations/sql/0021_environment_samples.sql``). A row says that a
source published a value for a quantity, over an interval, at a place or for
the globe, and when that value became available.

**Written only by ``meridian-ingest``, read only by the snapshot export.** No
scheduling or reception path imports this module: a feature reads these values
from a dataset snapshot, never from the live table, so an outage of every
source changes nothing about what is scheduled (D-131, D-132).

**Append-only, and a revision is a row.** A later fetch that republishes an
interval with a different value is a different artefact, so its row has its own
``record_id`` and a later ``published_at``; nothing here updates (D-222).

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-131, D-140, D-221, D-222.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.archive_observations import NormalisationDisagreementError
from meridian.store.stations import Connection

__all__ = [
    "EnvironmentSampleArrival",
    "NewEnvironmentSample",
    "NormalisationDisagreementError",
    "StoredEnvironmentSample",
    "find_environment_samples_for_record",
    "insert_environment_sample",
]


@dataclass(frozen=True, slots=True)
class NewEnvironmentSample:
    """One published value in insertable form. ``loaded_at`` is the column's."""

    record_id: int
    source_id: str
    transformation_version: str
    series_key: str
    content_sha256: bytes
    quantity: str
    value_unit: str
    observed_from: datetime
    observed_to: datetime
    published_at: datetime
    published_basis: str
    """``source_declared`` or ``retrieved`` (D-222)."""

    product: str
    value: float | None = None
    missing_reason: str | None = None
    """Set exactly when ``value`` is None: missing is recorded, never zero."""

    lat_deg: float | None = None
    lon_deg: float | None = None
    footprint_m: float | None = None
    quality: str | None = None


@dataclass(frozen=True, slots=True)
class StoredEnvironmentSample:
    """One row of ``environment_samples`` as read back."""

    sample_id: int
    record_id: int
    source_id: str
    transformation_version: str
    series_key: str
    content_sha256: bytes
    quantity: str
    value: float | None
    missing_reason: str | None
    value_unit: str
    observed_from: datetime
    observed_to: datetime
    published_at: datetime
    published_basis: str
    product: str
    lat_deg: float | None
    lon_deg: float | None
    footprint_m: float | None
    quality: str | None
    loaded_at: datetime


@dataclass(frozen=True, slots=True)
class EnvironmentSampleArrival:
    """The stored id, and whether this call was what wrote it."""

    sample_id: int
    written: bool


@dataclass(frozen=True, slots=True)
class _Existing:
    sample_id: int
    content_sha256: bytes


@dataclass(frozen=True, slots=True)
class _SampleId:
    sample_id: int


_COLUMNS = (
    "record_id, source_id, transformation_version, series_key, content_sha256,"
    " quantity, value, missing_reason, value_unit, observed_from, observed_to,"
    " published_at, published_basis, product, lat_deg, lon_deg, footprint_m,"
    " quality"
)


def insert_environment_sample(
    conn: Connection, sample: NewEnvironmentSample
) -> EnvironmentSampleArrival:
    """Store one published value, or return the id it already has.

    Args:
        conn: An open connection.
        sample: The value as its artefact normalised to.

    Returns:
        The stored id, and whether this call wrote it.

    Raises:
        NormalisationDisagreementError: This key is already stored under this
            transformation version with a different body, which means the
            normaliser is not deterministic (D-142).
    """
    with conn.transaction(), conn.cursor(row_factory=class_row(_SampleId)) as cur:
        cur.execute(
            f"insert into environment_samples ({_COLUMNS})"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s)"
            " on conflict on constraint environment_sample_unique do nothing"
            " returning sample_id",
            (
                sample.record_id,
                sample.source_id,
                sample.transformation_version,
                sample.series_key,
                sample.content_sha256,
                sample.quantity,
                sample.value,
                sample.missing_reason,
                sample.value_unit,
                sample.observed_from,
                sample.observed_to,
                sample.published_at,
                sample.published_basis,
                sample.product,
                sample.lat_deg,
                sample.lon_deg,
                sample.footprint_m,
                sample.quality,
            ),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return EnvironmentSampleArrival(inserted.sample_id, written=True)
        return _existing(conn, sample)


def _existing(
    conn: Connection, sample: NewEnvironmentSample
) -> EnvironmentSampleArrival:
    """The row an insert conflicted with, once it is known to agree."""
    with conn.cursor(row_factory=class_row(_Existing)) as cur:
        cur.execute(
            "select sample_id, content_sha256 from environment_samples"
            " where record_id = %s and series_key = %s"
            " and transformation_version = %s",
            (sample.record_id, sample.series_key, sample.transformation_version),
        )
        stored = cur.fetchone()
    if stored is None:  # pragma: no cover — the conflict proves the row
        message = "insert conflicted but the conflicting row is not readable"
        raise RuntimeError(message)
    if stored.content_sha256 != sample.content_sha256:
        message = (
            f"{sample.source_id}/{sample.series_key} already stored under "
            f"{sample.transformation_version} with a different body — the "
            "normaliser is not deterministic (docs/DECISIONS.md D-142)"
        )
        raise NormalisationDisagreementError(message)
    return EnvironmentSampleArrival(stored.sample_id, written=False)


def find_environment_samples_for_record(
    conn: Connection, record_id: int
) -> list[StoredEnvironmentSample]:
    """Every value normalised out of one artefact, in a stable order.

    Args:
        conn: An open connection. Read-only.
        record_id: The artefact.

    Returns:
        Its samples by quantity, interval and key, every transformation
        version included.
    """
    with conn.cursor(row_factory=class_row(StoredEnvironmentSample)) as cur:
        cur.execute(
            f"select sample_id, {_COLUMNS}, loaded_at"
            " from environment_samples where record_id = %s"
            " order by quantity, observed_from, series_key, transformation_version",
            (record_id,),
        )
        return cur.fetchall()

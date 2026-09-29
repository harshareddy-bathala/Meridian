"""Published values as a raw snapshot holds them, typed once for every reader.

``environment_samples.jsonl`` is what Stage 31's ingest normalised, frozen at
export (D-143). Two readers use it: the prediction module's conditions group,
and Stage 32's regional series. Both read it through here, so a malformed row
is refused the same way and both agree on which row a value is.

**One row per value, even when an artefact was normalised twice.** Re-running
a normaliser under a new version appends beside the old rows (D-140), so a
snapshot can hold two rows for one ``(record_id, series_key)``. The reader
keeps the one loaded last — the highest ``sample_id`` — which is a property of
the snapshot and not of when it is read, so every reader of one snapshot sees
the same values.

**A snapshot from before Stage 31 has no such file**, and reads as holding no
values: every feature drawn from them is then missing, which is the truth.

Reference: docs/DECISIONS.md D-140, D-143, D-221, D-222.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    instant,
    integer,
    jsonl_rows,
    optional_number,
    text,
)

__all__ = ["ENVIRONMENT_FILE", "EnvironmentSample", "read_environment_samples"]

ENVIRONMENT_FILE = "environment_samples.jsonl"


@dataclass(frozen=True, slots=True)
class EnvironmentSample:
    """One published value, with enough of its provenance to be cited."""

    sample_id: int
    record_id: int
    source_id: str
    series_key: str
    quantity: str
    value: float | None
    missing_reason: str | None
    value_unit: str
    observed_from: datetime
    observed_to: datetime
    published_at: datetime
    product: str
    lat_deg: float | None
    lon_deg: float | None
    footprint_m: float | None
    quality: str | None


def read_environment_samples(
    files: Mapping[str, bytes],
) -> tuple[EnvironmentSample, ...]:
    """Every published value a raw snapshot holds, one row per value.

    Args:
        files: The raw snapshot's files, by name, as read and verified.

    Returns:
        The values in ``sample_id`` order; empty for a snapshot exported
        before Stage 31.

    Raises:
        MalformedSnapshotError: A row is the wrong shape, or says it is both
            present and missing.
    """
    data = files.get(ENVIRONMENT_FILE)
    if data is None:
        return ()
    latest: dict[tuple[int, str], EnvironmentSample] = {}
    for row in jsonl_rows(data, ENVIRONMENT_FILE):
        sample = _sample(row)
        key = (sample.record_id, sample.series_key)
        held = latest.get(key)
        if held is None or sample.sample_id > held.sample_id:
            latest[key] = sample
    return tuple(sorted(latest.values(), key=lambda one: one.sample_id))


def _sample(row: Mapping[str, object]) -> EnvironmentSample:
    value = optional_number(row, "value")
    reason = row.get("missing_reason")
    if (value is None) == (reason is None):
        message = f"sample {row.get('sample_id')} is neither a value nor a stated gap"
        raise MalformedSnapshotError(message)
    return EnvironmentSample(
        sample_id=integer(row, "sample_id"),
        record_id=integer(row, "record_id"),
        source_id=text(row, "source_id"),
        series_key=text(row, "series_key"),
        quantity=text(row, "quantity"),
        value=value,
        missing_reason=None if reason is None else str(reason),
        value_unit=text(row, "value_unit"),
        observed_from=instant(row, "observed_from"),
        observed_to=instant(row, "observed_to"),
        published_at=instant(row, "published_at"),
        product=text(row, "product"),
        lat_deg=optional_number(row, "lat_deg"),
        lon_deg=optional_number(row, "lon_deg"),
        footprint_m=optional_number(row, "footprint_m"),
        quality=None if row.get("quality") is None else str(row.get("quality")),
    )

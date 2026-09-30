"""One value a public product published about one place and one interval.

Stage 31's sources — indices, conditions, composites, detections — all reduce
to the same thing: a number, what it measures, when and where it describes, and
**when it became available**. That last column is the one everything else
rests on. The value a pass may use is the one published before it (D-131), so
a sample that cannot say when it was published cannot be a feature.

**``published_at`` is never later than our own retrieval.** Where an artefact
declares when it was produced, and that is earlier than when we fetched it,
the declaration is used and ``published_basis`` says ``source_declared``.
Otherwise the value was published, as far as we can prove, when we retrieved
it — ``retrieved``. A value fetched after a pass is never that pass's feature,
however probable it is that the source had it earlier (D-222).

**A missing value stays missing.** A fill value in the source becomes a sample
with no value and a reason, never a zero and never the previous value. It is
stored, rather than dropped, because "the source said it had nothing for this
hour" and "we never asked" are different facts, and the pre-pass rule must be
able to tell a published gap from an unpublished one (D-221).

Reference: docs/DECISIONS.md D-131, D-133, D-221, D-222.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.raw_manifest import canonical_sha256, instant

__all__ = [
    "PUBLISHED_BASES",
    "QUANTITY",
    "NormalisedSample",
    "published_bound",
]

QUANTITY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
"""Lowercase free text, as ``DATA-MODEL.md`` leaves the vocabulary open."""

PUBLISHED_BASES = ("source_declared", "retrieved")

_MAX_LAT, _MAX_LON = 90.0, 180.0


@dataclass(frozen=True, slots=True)
class NormalisedSample:
    """One published value, before it has a row.

    Raises:
        NormalisationError: A field is blank or outside its vocabulary, an
            instant is naive, an interval runs backwards, publication is dated
            after nothing could have published it, a value and a missing
            reason are both or neither given, or a location is half given.
    """

    series_key: str
    """What makes this sample unique inside its artefact, in the source's terms
    — an index's interval, a detection's time and place, a pixel and a date."""

    quantity: str
    value_unit: str
    observed_from: datetime
    observed_to: datetime
    """The interval the value describes, closed at both ends. Equal for an
    instantaneous detection."""

    published_at: datetime
    published_basis: str
    product: str
    """The source's own product name and version, as published — what a
    series cites beside every point (Stage 32)."""

    value: float | None = None
    missing_reason: str | None = None
    lat_deg: float | None = None
    lon_deg: float | None = None
    footprint_m: float | None = None
    """The side of the square the value stands for, where it stands for one."""

    quality: str | None = None
    """The source's own quality flag, verbatim, where it publishes one."""

    def __post_init__(self) -> None:
        """Hold the record to what ``environment_samples`` would hold it to."""
        for name in ("series_key", "value_unit", "product"):
            if not str(getattr(self, name)).strip():
                message = f"a sample's {name} is empty"
                raise NormalisationError(message)
        if not QUANTITY.fullmatch(self.quantity):
            message = f"quantity {self.quantity!r} is not lowercase words"
            raise NormalisationError(message)
        if self.published_basis not in PUBLISHED_BASES:
            message = f"published_basis {self.published_basis!r} is unknown"
            raise NormalisationError(message)
        _instants(self)
        _value(self)
        _place(self)

    @property
    def content_sha256(self) -> bytes:
        """The digest two normalisations of this sample are compared by."""
        return canonical_sha256(
            {
                "series_key": self.series_key,
                "quantity": self.quantity,
                "value": self.value,
                "missing_reason": self.missing_reason,
                "value_unit": self.value_unit,
                "observed_from": instant(self.observed_from),
                "observed_to": instant(self.observed_to),
                "published_at": instant(self.published_at),
                "published_basis": self.published_basis,
                "product": self.product,
                "lat_deg": self.lat_deg,
                "lon_deg": self.lon_deg,
                "footprint_m": self.footprint_m,
                "quality": self.quality,
            }
        )


def published_bound(
    declared: datetime | None, retrieved_at: datetime
) -> tuple[datetime, str]:
    """When a value became available, as far as the artefact can prove.

    Args:
        declared: When the artefact says it was produced, or None.
        retrieved_at: When we fetched it, from its manifest.

    Returns:
        The instant and its basis: the declaration where it precedes the
        retrieval, the retrieval otherwise. A declaration *after* our own
        fetch is a clock somebody got wrong, and the earlier of two instants
        is the only one we can defend (D-222).
    """
    if declared is not None and declared <= retrieved_at:
        return declared, "source_declared"
    return retrieved_at, "retrieved"


def _instants(sample: NormalisedSample) -> None:
    for name in ("observed_from", "observed_to", "published_at"):
        moment = getattr(sample, name)
        if moment.tzinfo is None:
            message = f"{sample.series_key}: {name} is naive; every instant is UTC"
            raise NormalisationError(message)
    if sample.observed_to < sample.observed_from:
        message = f"{sample.series_key}: observed_to precedes observed_from"
        raise NormalisationError(message)


def _value(sample: NormalisedSample) -> None:
    """A value or a reason it is missing: exactly one."""
    if (sample.value is None) == (sample.missing_reason is None):
        message = (
            f"{sample.series_key}: give a value or a missing_reason, exactly one — "
            "a missing value is recorded as missing, never as zero (D-221)"
        )
        raise NormalisationError(message)
    if sample.value is not None and not math.isfinite(sample.value):
        message = f"{sample.series_key}: {sample.value} is not a finite number"
        raise NormalisationError(message)


def _place(sample: NormalisedSample) -> None:
    if (sample.lat_deg is None) != (sample.lon_deg is None):
        message = f"{sample.series_key}: half a location is not a place"
        raise NormalisationError(message)
    if sample.lat_deg is not None and not -_MAX_LAT <= sample.lat_deg <= _MAX_LAT:
        message = f"{sample.series_key}: latitude {sample.lat_deg} is off the globe"
        raise NormalisationError(message)
    if sample.lon_deg is not None and not -_MAX_LON <= sample.lon_deg <= _MAX_LON:
        message = f"{sample.series_key}: longitude {sample.lon_deg} is off the globe"
        raise NormalisationError(message)
    if sample.footprint_m is not None and sample.footprint_m <= 0:
        message = f"{sample.series_key}: a footprint of {sample.footprint_m} m"
        raise NormalisationError(message)

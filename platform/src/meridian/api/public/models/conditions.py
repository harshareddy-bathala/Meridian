"""The latest published conditions, as the public API states them (D-331).

Built from ``store.conditions_reads.PublishedSample``: the newest Kp, and the
newest cloud cover at each point an operator asked about, each with when it
was published, what it describes, and its source's name, licence and terms, so
a figure on a screen is always credited (D-134).

**Measured by others, and said so.** Every value here is a public source's, not
Meridian's, and not simulated. It has no ``simulated`` field because no
simulated value can reach ``environment_samples``: only ``meridian-ingest``
writes it, from published artefacts (D-138).

**A point is published to one decimal of a degree.** An operator often asks
about a station's own position, rounded to two decimals before it is sent
(D-220), and MSP lets a station publish its position as coarsely as one
(D-082). One decimal is never finer than any station may have declared.

**No Kp is a stated absence**, ``{"status": "not_measured", "reason": …}``,
never a null where a number belongs (D-086, D-187).

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.api.public.models.reliability import NotMeasuredFigure
from meridian.store.conditions_reads import PublishedSample

__all__ = [
    "NO_KP_REASON",
    "POINT_DECIMALS",
    "PublicConditions",
    "PublicSample",
    "PublicSampleSource",
]

POINT_DECIMALS = 1
"""Decimal places a sample's point is published to: MSP's coarsest (D-082)."""

NO_KP_REASON = (
    "No planetary K index has been ingested. `meridian-ingest follow` on the ingest"
    " machine keeps it current (D-225)."
)


class PublicSampleSource(BaseModel):
    """Who published a value, and on what terms."""

    source_id: str
    name: str
    licence: str
    terms_url: str


class PublicSample(BaseModel):
    """One published value, or a published gap, and its provenance."""

    quantity: str
    value: float | None
    """Null only beside a ``missing_reason``: the source published a gap."""
    missing_reason: str | None
    unit: str
    observed_from: datetime
    observed_to: datetime
    published_at: datetime
    published_basis: Literal["source_declared", "retrieved"]
    product: str
    lat_deg: float | None
    lon_deg: float | None
    """To one decimal of a degree; null for a value with no place, as Kp."""
    source: PublicSampleSource

    @classmethod
    def from_row(cls, row: PublishedSample) -> Self:
        """Publish one stored sample."""
        return cls(
            quantity=row.quantity,
            value=row.value,
            missing_reason=row.missing_reason,
            unit=row.value_unit,
            observed_from=row.observed_from,
            observed_to=row.observed_to,
            published_at=row.published_at,
            published_basis=_basis(row.published_basis),
            product=row.product,
            lat_deg=_point(row.lat_deg),
            lon_deg=_point(row.lon_deg),
            source=PublicSampleSource(
                source_id=row.source_id,
                name=row.source_name,
                licence=row.licence,
                terms_url=row.terms_url,
            ),
        )


class PublicConditions(BaseModel):
    """The latest Kp, and the latest cloud cover at each point asked about."""

    status: Literal["computed"] = "computed"
    as_of: datetime
    kp: PublicSample | NotMeasuredFigure
    cloud_cover: list[PublicSample]
    """Empty when no point's forecast covers ``as_of``."""

    @classmethod
    def from_rows(
        cls, as_of: datetime, kp: list[PublishedSample], cloud: list[PublishedSample]
    ) -> Self:
        """Publish what was read; the newest Kp of any point, if there is one."""
        latest = max(
            kp, key=lambda one: (one.observed_from, one.published_at), default=None
        )
        return cls(
            as_of=as_of,
            kp=NotMeasuredFigure(reason=NO_KP_REASON)
            if latest is None
            else PublicSample.from_row(latest),
            cloud_cover=[PublicSample.from_row(one) for one in cloud],
        )


def _point(value: float | None) -> float | None:
    """A coordinate to :data:`POINT_DECIMALS`, or ``None``."""
    return None if value is None else round(value, POINT_DECIMALS)


def _basis(value: str) -> Literal["source_declared", "retrieved"]:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for basis in ("source_declared", "retrieved"):
        if value == basis:
            return basis
    raise ValueError(f"environment_samples.published_basis holds {value!r}")

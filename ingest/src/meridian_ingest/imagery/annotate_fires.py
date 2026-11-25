"""Fire markers: FIRMS detections inside the box within a day of the scene.

**Which records.** FIRMS is fetched one UTC day at a time over a configured
box (Stage 31), and each fetch is a raw artefact whose provenance says which
day and which box it covers. A scene acquired at *t* is marked with the
detections from *t* − 1 day to *t* + 1 day, so the days that window touches
are read: for each, the newest artefact covering that whole day over a box
that holds the area. A newer fetch of a day supersedes an older one, because
FIRMS revises a near-real-time day as it settles.

**Re-read by the normaliser that loads them.** The detections come from the
same pure :class:`~meridian_ingest.adapters.public.firms.FirmsNormaliser` that
``load`` uses, never re-parsed here, so a marker and a stored sample are the
same reading of the same bytes. Its samples come one per data row in order,
so a marker cites its artefact's digest and the row it came from.

**A day nobody fetched is not a day without fire.** The record says which of
the window's days were held, and the caption says it: no detection on a held
day is "none detected", while a day not held is "not checked" (rule 7's
distinction, applied to someone else's records).

At most :data:`MAX_FIRES` are marked, the most powerful first, and the
caption says how many were held.

Record-only: no image library, no NumPy (D-371).

Reference: docs/DECISIONS.md D-133, D-221, D-374.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from meridian_ingest.adapters.public.firms import FIRMS, FirmsNormaliser
from meridian_ingest.imagery.model import Annotation, Bounds, Citation, Input, Point
from meridian_ingest.normalise.samples import NormalisedSample
from meridian_ingest.raw_store import RawStore, StoredArtefact

__all__ = ["FIRE_WINDOW", "MAX_FIRES", "FireRecords", "fire_records"]

FIRE_WINDOW = timedelta(days=1)
MAX_FIRES = 30
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/"

_DAY = timedelta(days=1)
_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}


@dataclass(frozen=True, slots=True)
class FireRecords:
    """The fires marked on one scene, and what was checked to find them."""

    days: tuple[date, ...]
    """Every UTC day the window touches."""

    held: tuple[date, ...]
    """The days a FIRMS artefact covering the box was held for."""

    detected: int
    """Detections inside the box and the window, before :data:`MAX_FIRES`."""

    annotations: tuple[Annotation, ...]
    inputs: tuple[Input, ...]

    @property
    def missing(self) -> tuple[date, ...]:
        """The days in the window no record was held for."""
        return tuple(one for one in self.days if one not in self.held)


def fire_records(store: RawStore, bounds: Bounds, acquired_at: datetime) -> FireRecords:
    """The FIRMS detections to mark on a scene of ``bounds`` acquired then."""
    first = (acquired_at - FIRE_WINDOW).astimezone(UTC).date()
    last = (acquired_at + FIRE_WINDOW).astimezone(UTC).date()
    days = tuple(first + timedelta(days=n) for n in range((last - first).days + 1))
    chosen = _newest_per_day(store, bounds, days)
    found: list[tuple[NormalisedSample, StoredArtefact, int]] = []
    for day in sorted(chosen):
        artefact = chosen[day]
        batch = FirmsNormaliser().normalise(artefact)
        for row, sample in enumerate(batch.samples, start=1):
            if _marks(sample, bounds, acquired_at):
                found.append((sample, artefact, row))
    found.sort(key=_strongest_first)
    shown = sorted(found[:MAX_FIRES], key=lambda one: _in_time(one[0]))
    return FireRecords(
        days=days,
        held=tuple(sorted(chosen)),
        detected=len(found),
        annotations=tuple(_annotation(*one) for one in shown),
        inputs=tuple(
            Input("firms", chosen[day].raw_path, chosen[day].manifest.sha256.hex())
            for day in sorted(chosen)
        ),
    )


def _newest_per_day(
    store: RawStore, bounds: Bounds, days: tuple[date, ...]
) -> dict[date, StoredArtefact]:
    """For each day, the newest artefact covering all of it over the box."""
    chosen: dict[date, StoredArtefact] = {}
    for raw_path in store.scan(FIRMS.source_id):
        artefact = store.read(raw_path)
        provenance = artefact.manifest.provenance
        start, end = provenance.valid_from, provenance.valid_to
        if provenance.payload_kind != "data" or start is None or end is None:
            continue
        start = start.astimezone(UTC)
        if end - start != _DAY or start.time() != datetime.min.time():
            continue
        if start.date() in days and _holds(provenance.spatial_extent, bounds):
            # Name order is retrieval order, so a later fetch replaces it.
            chosen[start.date()] = artefact
    return chosen


def _holds(extent: dict[str, float] | None, bounds: Bounds) -> bool:
    """Whether a fetched box covers the whole area."""
    if extent is None:
        return False
    return (
        extent["west"] <= bounds.west
        and extent["south"] <= bounds.south
        and extent["east"] >= bounds.east
        and extent["north"] >= bounds.north
    )


def _marks(sample: NormalisedSample, bounds: Bounds, acquired_at: datetime) -> bool:
    if sample.lat_deg is None or sample.lon_deg is None:
        return False
    inside = bounds.contains(Point(sample.lat_deg, sample.lon_deg))
    return inside and abs(sample.observed_from - acquired_at) <= FIRE_WINDOW


def _strongest_first(
    found: tuple[NormalisedSample, StoredArtefact, int],
) -> tuple[float, tuple[datetime, float, float]]:
    sample = found[0]
    power = -sample.value if sample.value is not None else math.inf
    return power, _in_time(sample)


def _in_time(sample: NormalisedSample) -> tuple[datetime, float, float]:
    return sample.observed_from, -(sample.lat_deg or 0.0), sample.lon_deg or 0.0


def _annotation(
    sample: NormalisedSample, artefact: StoredArtefact, row: int
) -> Annotation:
    power = (
        "no fire radiative power given"
        if sample.value is None
        else f"{sample.value:.1f} MW fire radiative power"
    )
    return Annotation(
        kind="fire",
        geometry=Point(sample.lat_deg or 0.0, sample.lon_deg or 0.0),
        label="Fire",
        text=(
            f"Active fire detected {sample.observed_from:%d %b %Y %H:%M} UTC by "
            f"VIIRS on Suomi NPP, {power}, {_confidence(sample.quality)} confidence"
        ),
        citations=(
            Citation(
                FIRMS.source_id,
                f"{artefact.manifest.sha256.hex()[:12]} row {row}",
                FIRMS_URL,
                FIRMS.licence,
            ),
        ),
    )


def _confidence(quality: str | None) -> str:
    """FIRMS' confidence letter in words, from the normaliser's quality text."""
    for part in (quality or "").split(";"):
        name, _, value = part.partition("=")
        if name == "confidence":
            return _CONFIDENCE.get(value.lower(), value or "unstated")
    return "unstated"

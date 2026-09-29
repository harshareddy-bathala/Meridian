"""Active fires: NASA FIRMS near-real-time VIIRS detections inside a box.

**What a row is.** One 375 m VIIRS pixel the fire algorithm flagged as
containing an active fire at an overpass, with its fire radiative power in
megawatts and a confidence of low, nominal or high. It is a *detection*, not a
fire: cloud hides fires, and a small or cool one is missed. Stage 32 counts
them per area and says so.

**No detection is not no data.** A day and box that were fetched and hold no
rows is a fetched day with zero detections; a day never fetched is a gap. The
artefact records the day it covers in ``valid_from``/``valid_to`` and the box in
``spatial_extent``, so Stage 32 can tell the two apart from the provenance
alone (D-221).

**Access.** A free ``MAP_KEY``, which the service counts — 5 000 transactions
in each ten-minute window. The key sits in the URL path, so the adapter plans
:data:`~meridian_ingest.retrieval.KEY_PLACEHOLDER` there and the retriever
substitutes it at the socket and redacts it from anything printed (D-223).

**Format.** CSV with a header: ``latitude, longitude, bright_ti4, scan, track,
acq_date, acq_time, satellite, instrument, confidence, version, bright_ti5,
frp, daynight``. ``acq_time`` is ``HHMM`` in UTC and may arrive without its
leading zeros.

Reference: docs/DECISIONS.md D-133, D-220, D-221, D-223.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, date, datetime, timedelta

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import (
    days_between,
    missing_unless,
    number_or_none,
    require_bbox,
    retrieved_at,
    version_from_headers,
)
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample, published_bound
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import KEY_PLACEHOLDER, RemoteArtefact, RetrievedArtefact

__all__ = ["FIRMS", "FirmsAdapter", "FirmsNormaliser"]

FIRMS = SourceDescriptor(
    source_id="nasa_firms",
    source_class="regional_product",
    name="NASA FIRMS active fire detections (VIIRS S-NPP, near real time)",
    licence="NASA open data; no restrictions on use, citation requested",
    terms_url="https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
    attribution_entry="NASA FIRMS active fire detections",
    access_constraint="key_counted",
)

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SENSOR = "VIIRS_SNPP_NRT"
TRANSFORMATION_VERSION = "firms-area-1"
_COLUMNS = frozenset({"latitude", "longitude", "scan", "track", "acq_date"})


class FirmsAdapter:
    """One request per UTC day for the configured box."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return FIRMS

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Each day in the interval, or the most recent day when none is given.

        Raises:
            ValueError: No box is configured.
        """
        bbox = require_bbox(request, FIRMS.source_id)
        box = bbox.as_text()
        extent = {
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
        }
        if request.since is None or request.until is None:
            return (self._remote(box, extent, None),)
        days = days_between(request.since, request.until)
        return tuple(self._remote(box, extent, day) for day in days[: request.limit])

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """Computed on request, so named by when the server served it."""
        return version_from_headers(retrieved)

    @staticmethod
    def _remote(box: str, extent: dict[str, float], day: date | None) -> RemoteArtefact:
        if day is None:
            return RemoteArtefact(
                url=f"{BASE}/{KEY_PLACEHOLDER}/{SENSOR}/{box}/1",
                original_identifier=f"{SENSOR}/{box}/latest",
                payload_kind="data",
                spatial_extent=extent,
            )
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return RemoteArtefact(
            url=f"{BASE}/{KEY_PLACEHOLDER}/{SENSOR}/{box}/1/{day.isoformat()}",
            original_identifier=f"{SENSOR}/{box}/{day.isoformat()}",
            payload_kind="data",
            valid_from=start,
            valid_to=start + timedelta(days=1),
            spatial_extent=extent,
        )


class FirmsNormaliser:
    """A FIRMS area CSV as one sample per detection."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every detection, its fire radiative power the value.

        Raises:
            NormalisationError: The bytes are not a FIRMS CSV.
        """
        refuse_a_tile(artefact.manifest.provenance)
        try:
            text = artefact.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            message = f"{artefact.raw_path} is not UTF-8 text"
            raise NormalisationError(message) from exc
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or not set(reader.fieldnames) >= _COLUMNS:
            message = f"{artefact.raw_path} is not a FIRMS area CSV"
            raise NormalisationError(message)
        published_at, basis = published_bound(None, retrieved_at(artefact))
        samples = tuple(_detection(row, published_at, basis) for row in reader)
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=samples
        )


def _detection(
    row: dict[str, str], published_at: datetime, basis: str
) -> NormalisedSample:
    lat, lon = _required(row, "latitude"), _required(row, "longitude")
    at = _acquired(row)
    frp = number_or_none(row.get("frp"))
    scan, track = _required(row, "scan"), _required(row, "track")
    satellite = row.get("satellite", "").strip() or "unknown"
    return NormalisedSample(
        series_key=f"{at.isoformat()}@{lat:.5f},{lon:.5f}/{satellite}",
        quantity="fire_radiative_power",
        value_unit="MW",
        observed_from=at,
        observed_to=at,
        published_at=published_at,
        published_basis=basis,
        product=f"{SENSOR} {row.get('version', '').strip() or 'unversioned'}",
        value=frp,
        missing_reason=missing_unless(frp, "detection without a power"),
        lat_deg=lat,
        lon_deg=lon,
        footprint_m=math.sqrt(scan * track) * 1000.0,
        quality=f"confidence={row.get('confidence', '').strip() or '?'};"
        f"daynight={row.get('daynight', '').strip() or '?'}",
    )


def _required(row: dict[str, str], name: str) -> float:
    value = number_or_none(row.get(name))
    if value is None:
        message = f"a detection has no {name}"
        raise NormalisationError(message)
    return value


def _acquired(row: dict[str, str]) -> datetime:
    """``acq_date`` and ``acq_time`` as one UTC instant."""
    hhmm = (row.get("acq_time") or "").strip().zfill(4)
    try:
        return datetime.strptime(
            f"{row.get('acq_date', '').strip()} {hhmm}", "%Y-%m-%d %H%M"
        ).replace(tzinfo=UTC)
    except ValueError as exc:
        message = f"{row.get('acq_date')!r} {row.get('acq_time')!r} is not a time"
        raise NormalisationError(message) from exc

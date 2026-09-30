"""Vegetation: MODIS 16-day NDVI around a point, from the ORNL DAAC subset service.

**What a value is.** The normalised difference vegetation index of one 250 m
MODIS pixel, composited over sixteen days by choosing the best observation in
the window. A composite says what the ground looked like across a fortnight; it
does not say what it looked like on any one day in it, and Stage 32 presents it
that way.

**Where.** The service returns a small grid of pixels around the point asked
for, in the MODIS sinusoidal projection. Each pixel's centre is converted to
latitude and longitude with the sinusoidal inverse on the MODIS sphere, and
stored as its own sample with a 250 m footprint.

**When it was published.** Each composite carries ``proc_date``, when the
granule was produced, so ``published_at`` is that where it precedes our fetch
(D-222). A reprocessed collection is a new ``proc_date`` and a new row.

**Format.** JSON: ``xllcorner``, ``yllcorner`` and ``cellsize`` in metres,
``nrows``, ``ncols``, ``scale``, and ``subset`` — one entry per composite with
``modis_date`` (``AYYYYDDD``), ``calendar_date``, ``proc_date``
(``YYYYDDDHHMMSS``) and ``data``, the pixels row by row from the north-west.
``-3000`` is the fill value. The service answers at most ten composites per
request, so a longer interval is planned as several.

Reference: docs/DECISIONS.md D-220, D-221, D-222.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import (
    missing_unless,
    number_or_none,
    parse_json,
    require_interval,
    require_points,
    retrieved_at,
    version_from_headers,
)
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample, published_bound
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "ORNL_NDVI",
    "OrnlNdviAdapter",
    "OrnlNdviNormaliser",
    "sinusoidal_to_geographic",
]

ORNL_NDVI = SourceDescriptor(
    source_id="ornl_modis_ndvi",
    source_class="regional_product",
    name="ORNL DAAC MODIS/VIIRS land product subsets, MOD13Q1 NDVI",
    licence="NASA open data; no restrictions on use, citation requested",
    terms_url="https://daac.ornl.gov/LAND_VAL/guides/MODIS_Web_Service_C6_V2.html",
    attribution_entry="ORNL DAAC MODIS NDVI subsets",
    access_constraint="none",
)

BASE = "https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset"
BAND = "250m_16_days_NDVI"
FILL = -3000.0
COMPOSITE = timedelta(days=16)
CHUNK = timedelta(days=150)
"""Under ten 16-day composites, the service's ceiling per request."""

SPHERE_M = 6371007.181
"""The MODIS sinusoidal grid's sphere."""

TRANSFORMATION_VERSION = "ornl-ndvi-1"


def sinusoidal_to_geographic(x_m: float, y_m: float) -> tuple[float, float]:
    """A MODIS sinusoidal coordinate as latitude and longitude, in degrees."""
    lat = y_m / SPHERE_M
    lon = x_m / (SPHERE_M * math.cos(lat))
    return math.degrees(lat), math.degrees(lon)


class OrnlNdviAdapter:
    """A 3 × 3 km NDVI subset at each configured point."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return ORNL_NDVI

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Each point, over the interval in chunks the service will answer.

        Raises:
            ValueError: No points, or no interval.
        """
        require_points(request, ORNL_NDVI.source_id)
        since, until = require_interval(request, ORNL_NDVI.source_id)
        planned = []
        for point in request.points:
            sent = point.sent()
            start = since
            while start < until:
                end = min(start + CHUNK, until)
                planned.append(_remote(sent.lat_deg, sent.lon_deg, start, end))
                start = end
        return tuple(planned[: request.limit])

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """Computed on request, so named by when the server served it."""
        return version_from_headers(retrieved)


def _remote(lat: float, lon: float, start: datetime, end: datetime) -> RemoteArtefact:
    first, last = _modis_date(start), _modis_date(end)
    query = (
        f"latitude={lat}&longitude={lon}&band={BAND}"
        f"&startDate={first}&endDate={last}&kmAboveBelow=1&kmLeftRight=1"
    )
    return RemoteArtefact(
        url=f"{BASE}?{query}",
        original_identifier=f"MOD13Q1/{BAND}@{lat},{lon}/{first}-{last}",
        payload_kind="data",
        headers={"Accept": "application/json"},
        valid_from=start,
        valid_to=end,
    )


def _modis_date(moment: datetime) -> str:
    day = moment.astimezone(UTC)
    return f"A{day.year}{day.timetuple().tm_yday:03d}"


class OrnlNdviNormaliser:
    """An NDVI subset as one sample per pixel per composite."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every pixel of every composite, fill values kept as missing.

        Raises:
            NormalisationError: The response is not a subset, or a composite's
                pixels do not fill its grid.
        """
        refuse_a_tile(artefact.manifest.provenance)
        body = parse_json(artefact)
        if not isinstance(body, dict) or not isinstance(body.get("subset"), list):
            message = f"{artefact.raw_path} is not an ORNL subset response"
            raise NormalisationError(message)
        grid = _Grid.from_body(body)
        fetched = retrieved_at(artefact)
        samples: list[NormalisedSample] = []
        for composite in body["subset"]:
            if not isinstance(composite, dict):
                message = f"{artefact.raw_path}: a composite is not an object"
                raise NormalisationError(message)
            samples.extend(grid.samples(composite, fetched))
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=tuple(samples)
        )


class _Grid:
    """The subset's pixel grid, and how each pixel becomes a sample."""

    def __init__(
        self,
        corner: tuple[float, float],
        cell: float,
        shape: tuple[int, int],
        scale: float,
    ) -> None:
        self.corner, self.cell, self.shape, self.scale = corner, cell, shape, scale

    @classmethod
    def from_body(cls, body: dict[str, object]) -> _Grid:
        try:
            corner = (float(str(body["xllcorner"])), float(str(body["yllcorner"])))
            cell = float(str(body["cellsize"]))
            shape = (int(str(body["nrows"])), int(str(body["ncols"])))
            scale = float(str(body.get("scale", "1")))
        except (KeyError, ValueError) as exc:
            message = f"the subset's grid is not described: {exc}"
            raise NormalisationError(message) from exc
        return cls(corner, cell, shape, scale)

    def samples(
        self, composite: dict[str, object], fetched: datetime
    ) -> list[NormalisedSample]:
        data = composite.get("data")
        rows, cols = self.shape
        if not isinstance(data, list) or len(data) != rows * cols:
            message = f"composite {composite.get('modis_date')} does not fill its grid"
            raise NormalisationError(message)
        start = _calendar(composite.get("calendar_date"))
        published_at, basis = published_bound(
            _produced(composite.get("proc_date")), fetched
        )
        out = []
        for index, raw in enumerate(data):
            row, col = divmod(index, cols)
            lat, lon = sinusoidal_to_geographic(
                self.corner[0] + (col + 0.5) * self.cell,
                self.corner[1] + (rows - row - 0.5) * self.cell,
            )
            number = number_or_none(raw, fill=FILL)
            value = None if number is None else round(number * self.scale, 6)
            out.append(
                NormalisedSample(
                    series_key=f"{composite.get('modis_date')}:{row},{col}",
                    quantity="ndvi",
                    value_unit="1",
                    observed_from=start,
                    observed_to=start + COMPOSITE,
                    published_at=published_at,
                    published_basis=basis,
                    product=f"MOD13Q1.061 {BAND} tile {composite.get('tile')}",
                    value=value,
                    missing_reason=missing_unless(value, "fill value -3000"),
                    lat_deg=round(lat, 6),
                    lon_deg=round(lon, 6),
                    footprint_m=self.cell,
                )
            )
        return out


def _calendar(value: object) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        message = f"calendar_date {value!r} is not a date"
        raise NormalisationError(message) from exc


def _produced(value: object) -> datetime | None:
    """``proc_date`` as an instant, or None where the composite gave none."""
    if value is None:
        return None
    try:
        return datetime.strptime(str(value), "%Y%j%H%M%S").replace(tzinfo=UTC)
    except ValueError as exc:
        message = f"proc_date {value!r} is not YYYYDDDHHMMSS"
        raise NormalisationError(message) from exc

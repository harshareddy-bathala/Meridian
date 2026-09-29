"""Precipitation: daily corrected precipitation at a point, from NASA POWER.

**What a value is.** ``PRECTOTCORR``, the bias-corrected total precipitation
for one UTC day, in millimetres, from POWER's reanalysis-based daily series on
its half-degree grid. A modelled estimate for a grid cell, not a rain gauge at
the point, and Stage 32 says so beside every series drawn from it.

**Revision.** Recent days are filled from near-real-time inputs and later
replaced as the reanalysis catches up, so the same day is published more than
once; each differing fetch is a new artefact whose rows carry a later
``published_at``, and the earlier values stay (D-222).

**Format.** GeoJSON ``Feature``: ``geometry.coordinates`` is
``[longitude, latitude, elevation]`` of the cell, ``properties.parameter
.PRECTOTCORR`` maps ``YYYYMMDD`` to a value, ``header.fill_value`` (``-999``)
marks a day with none, and ``parameters.PRECTOTCORR.units`` names the unit.

Reference: docs/DECISIONS.md D-220, D-221, D-222.
"""

from __future__ import annotations

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

__all__ = ["POWER_PRECIPITATION", "PowerAdapter", "PowerNormaliser"]

POWER_PRECIPITATION = SourceDescriptor(
    source_id="nasa_power_precipitation",
    source_class="regional_product",
    name="NASA POWER daily point API, PRECTOTCORR",
    licence="NASA open data; no restrictions on use, citation requested",
    terms_url="https://power.larc.nasa.gov/docs/services/api/",
    attribution_entry="NASA POWER precipitation",
    access_constraint="none",
)

BASE = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETER = "PRECTOTCORR"
TRANSFORMATION_VERSION = "power-daily-1"
SENT_DECIMALS = 1
"""The grid is half a degree; a tenth is already finer than it answers."""


class PowerAdapter:
    """Daily precipitation at each configured point, over the interval."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return POWER_PRECIPITATION

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """One request per point covering every day in the interval.

        Raises:
            ValueError: No points, or no interval.
        """
        require_points(request, POWER_PRECIPITATION.source_id)
        since, until = require_interval(request, POWER_PRECIPITATION.source_id)
        first = since.astimezone(UTC).strftime("%Y%m%d")
        last = (until.astimezone(UTC) - timedelta(microseconds=1)).strftime("%Y%m%d")
        planned = []
        for point in request.points[: request.limit]:
            sent = point.sent(SENT_DECIMALS)
            query = (
                f"parameters={PARAMETER}&community=AG&longitude={sent.lon_deg}"
                f"&latitude={sent.lat_deg}&start={first}&end={last}&format=JSON"
            )
            planned.append(
                RemoteArtefact(
                    url=f"{BASE}?{query}",
                    original_identifier=(
                        f"{PARAMETER}@{sent.lat_deg},{sent.lon_deg}/{first}-{last}"
                    ),
                    payload_kind="data",
                    valid_from=since,
                    valid_to=until,
                )
            )
        return tuple(planned)

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """Computed on request, so named by when the server served it."""
        return version_from_headers(retrieved)


class PowerNormaliser:
    """A POWER daily point response as one sample per day."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every day in the response, fill values kept as missing.

        Raises:
            NormalisationError: The response is not this shape.
        """
        refuse_a_tile(artefact.manifest.provenance)
        body = parse_json(artefact)
        series, fill, unit, version = _parts(body, artefact.raw_path)
        lat, lon = _cell(body)
        published_at, basis = published_bound(None, retrieved_at(artefact))
        samples = []
        for day, raw in sorted(series.items()):
            start = _day(day)
            value = number_or_none(raw, fill=fill)
            samples.append(
                NormalisedSample(
                    series_key=f"{PARAMETER}:{day}",
                    quantity="precipitation",
                    value_unit=unit,
                    observed_from=start,
                    observed_to=start + timedelta(days=1),
                    published_at=published_at,
                    published_basis=basis,
                    product=f"NASA POWER {PARAMETER} ({version})",
                    value=value,
                    missing_reason=missing_unless(value, f"fill value {fill:g}"),
                    lat_deg=lat,
                    lon_deg=lon,
                )
            )
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=tuple(samples)
        )


def _parts(body: object, where: str) -> tuple[dict[str, object], float, str, str]:
    """The day series, its fill value, its unit and the API's version."""
    properties = _table(body, "properties")
    series = _table(_table(properties, "parameter"), PARAMETER)
    if not series:
        message = f"{where} is not a POWER daily point response with {PARAMETER}"
        raise NormalisationError(message)
    header = _table(body, "header")
    fill = number_or_none(header.get("fill_value"))
    unit = _table(_table(body, "parameters"), PARAMETER).get("units") or "mm/day"
    version = _table(header, "api").get("version") or "unversioned"
    return series, -999.0 if fill is None else fill, str(unit), str(version)


def _table(value: object, key: str) -> dict[str, object]:
    """One nested object, or an empty one where the response has none."""
    inner = value.get(key) if isinstance(value, dict) else None
    return (
        {str(name): item for name, item in inner.items()}
        if isinstance(inner, dict)
        else {}
    )


def _cell(body: object) -> tuple[float | None, float | None]:
    geometry = body.get("geometry") if isinstance(body, dict) else None
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < 2:  # noqa: PLR2004
        return None, None
    return number_or_none(coordinates[1]), number_or_none(coordinates[0])


def _day(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as exc:
        message = f"{text!r} is not a POWER day"
        raise NormalisationError(message) from exc

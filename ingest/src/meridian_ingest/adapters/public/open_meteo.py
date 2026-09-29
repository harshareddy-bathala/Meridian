"""Local atmosphere and aerosol: hourly model values at a point, from Open-Meteo.

Two sources behind one format. **Cloud cover** is the local atmospheric
conditions class: the reception verdict needs it to tell "the link failed" from
"the link worked and the sky was white" (D-131). **Aerosol optical depth** is
the aerosol class, from the CAMS global composition forecast that Open-Meteo
redistributes, for regional monitoring and as candidate context.

**What a value is.** A forecast, or for past hours the model's own estimate —
never a measurement at the station. Each hour is stored as an interval
``[t, t + 1 h]``, and each fetch republishes every hour it covers: the pre-pass
rule reads, for a pass's hour, the value of the latest fetch made *before* the
pass, which is the forecast an operator could have had (D-222).

**Where.** A point from the settings file, rounded to two decimals before it is
sent (:mod:`meridian_ingest.extent`); the response names the model cell it
answered from, and that cell's centre is what is stored.

**Format.** JSON: ``latitude``, ``longitude``, ``hourly_units`` and
``hourly`` — parallel arrays ``time`` (``YYYY-MM-DDTHH:MM``, UTC when asked for
``timezone=GMT``) and one per variable, ``null`` where the model has nothing.

Reference: docs/DECISIONS.md D-131, D-220, D-221, D-222.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import (
    missing_unless,
    number_or_none,
    parse_json,
    require_points,
    retrieved_at,
    utc,
    version_from_headers,
)
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample, published_bound
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "OPEN_METEO_AEROSOL",
    "OPEN_METEO_CLOUD",
    "HourlyAdapter",
    "HourlyNormaliser",
    "HourlyProduct",
]

_TERMS = "https://open-meteo.com/en/terms"
_LICENCE = "CC BY 4.0 (free API: non-commercial use only)"


@dataclass(frozen=True, slots=True)
class HourlyProduct:
    """One Open-Meteo variable, and how it is asked for and stored."""

    descriptor: SourceDescriptor
    endpoint: str
    variable: str
    quantity: str
    unit_if_blank: str
    product: str
    transformation_version: str


OPEN_METEO_CLOUD = HourlyProduct(
    descriptor=SourceDescriptor(
        source_id="open_meteo_cloud",
        source_class="atmospheric",
        name="Open-Meteo forecast API, hourly cloud cover",
        licence=_LICENCE,
        terms_url=_TERMS,
        attribution_entry="Open-Meteo cloud cover",
        access_constraint="none",
    ),
    endpoint="https://api.open-meteo.com/v1/forecast",
    variable="cloud_cover",
    quantity="cloud_cover",
    unit_if_blank="%",
    product="Open-Meteo forecast, best-match model, cloud_cover",
    transformation_version="open-meteo-hourly-1",
)

OPEN_METEO_AEROSOL = HourlyProduct(
    descriptor=SourceDescriptor(
        source_id="open_meteo_aerosol",
        source_class="regional_product",
        name="Open-Meteo air-quality API (CAMS), hourly aerosol optical depth",
        licence=_LICENCE + "; CAMS data under the Copernicus licence",
        terms_url=_TERMS,
        attribution_entry="Open-Meteo aerosol optical depth (CAMS)",
        access_constraint="none",
    ),
    endpoint="https://air-quality-api.open-meteo.com/v1/air-quality",
    variable="aerosol_optical_depth",
    quantity="aerosol_optical_depth",
    unit_if_blank="1",
    product="CAMS global composition forecast via Open-Meteo, aerosol_optical_depth",
    transformation_version="open-meteo-hourly-1",
)

PAST_DAYS = 1
FORECAST_DAYS = 3
"""Each fetch republishes yesterday and the next three days. Yesterday keeps a
gap in the timer from leaving an hour with no value at all; three days is as
far ahead as a scheduled pass is looked at."""


class HourlyAdapter:
    """One Open-Meteo variable at each configured point."""

    def __init__(self, product: HourlyProduct) -> None:
        """Serve ``product``. Nothing is fetched."""
        self._product = product

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return self._product.descriptor

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """One request per point: the recent past and the next days.

        Raises:
            ValueError: No points are configured.
        """
        require_points(request, self._product.descriptor.source_id)
        planned = []
        for point in request.points[: request.limit]:
            sent = point.sent()
            query = (
                f"latitude={sent.lat_deg}&longitude={sent.lon_deg}"
                f"&hourly={self._product.variable}&timezone=GMT"
                f"&past_days={PAST_DAYS}&forecast_days={FORECAST_DAYS}"
            )
            planned.append(
                RemoteArtefact(
                    url=f"{self._product.endpoint}?{query}",
                    original_identifier=(
                        f"{self._product.variable}@{sent.lat_deg},{sent.lon_deg}"
                    ),
                    payload_kind="data",
                )
            )
        return tuple(planned)

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """Computed on request, so named by when the server served it."""
        return version_from_headers(retrieved)


class HourlyNormaliser:
    """An Open-Meteo hourly response as one sample per hour."""

    def __init__(self, product: HourlyProduct) -> None:
        """Read ``product``'s variable."""
        self._product = product
        self.transformation_version = product.transformation_version

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every hour in the response, missing hours kept as missing.

        Raises:
            NormalisationError: The response is not this shape, or its arrays
                disagree in length.
        """
        refuse_a_tile(artefact.manifest.provenance)
        body = parse_json(artefact)
        if not isinstance(body, dict):
            message = f"{artefact.raw_path} is not a JSON object"
            raise NormalisationError(message)
        times, values = _series(body, self._product.variable, artefact.raw_path)
        lat = number_or_none(body.get("latitude"))
        lon = number_or_none(body.get("longitude"))
        unit = _unit(body, self._product)
        published_at, basis = published_bound(None, retrieved_at(artefact))
        samples = []
        for stamp, raw in zip(times, values, strict=True):
            start = utc(stamp)
            value = number_or_none(raw)
            samples.append(
                NormalisedSample(
                    series_key=f"{self._product.variable}:{start.isoformat()}",
                    quantity=self._product.quantity,
                    value_unit=unit,
                    observed_from=start,
                    observed_to=start + timedelta(hours=1),
                    published_at=published_at,
                    published_basis=basis,
                    product=self._product.product,
                    value=value,
                    missing_reason=missing_unless(value, "null in the response"),
                    lat_deg=lat,
                    lon_deg=lon,
                )
            )
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=tuple(samples)
        )


def _series(
    body: dict[str, object], variable: str, where: str
) -> tuple[list[object], list[object]]:
    hourly = body.get("hourly")
    if not isinstance(hourly, dict):
        message = f"{where} has no hourly block"
        raise NormalisationError(message)
    times, values = hourly.get("time"), hourly.get(variable)
    if not isinstance(times, list) or not isinstance(values, list):
        message = f"{where} has no hourly time and {variable} arrays"
        raise NormalisationError(message)
    if len(times) != len(values):
        message = f"{where}: {len(times)} times but {len(values)} values"
        raise NormalisationError(message)
    return times, values


def _unit(body: dict[str, object], product: HourlyProduct) -> str:
    units = body.get("hourly_units")
    stated = units.get(product.variable) if isinstance(units, dict) else None
    return (
        str(stated).strip() if stated and str(stated).strip() else product.unit_if_blank
    )

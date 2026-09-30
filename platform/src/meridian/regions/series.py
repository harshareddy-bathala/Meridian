"""What the ingested products say about one area over time.

Each point of a series is computed from published values in a snapshot — never
from a rendered tile (D-133) and never at runtime from a source — and carries
the artefacts it came from: their sources, product versions, record ids and the
time they were retrieved (Stage 32's test list, D-229).

Three shapes of product, three ways of placing one on an area:

* **Pixels** (``ndvi``, ``night_lights_radiance``): the mean of every pixel
  whose centre lies inside the area, per composite. An area smaller than a
  pixel takes the pixels whose footprint reaches it.
* **Cells** (``precipitation``, ``aerosol_optical_depth``, ``cloud_cover``):
  the model cells inside the area, or else the one nearest within
  ``nearest_km``, as a daily mean. The method says which.
* **Detections** (``fire_count``, ``fire_radiative_power_sum``): detections
  inside the area per UTC day — only for days a fetched artefact's box and day
  covered the whole area, fetched after the day ended. A covered day with none
  is **0**; a day nobody asked about, or asked about only while it was under
  way, is **absent**, never 0 (D-221).

**The latest revision of each value is used**, since a report describes the
world as the snapshot knew it. That is the opposite of a feature's pre-pass
rule, deliberately: a feature must not read the future, and a report must not
ignore a correction.

**A point with no value is missing, with its reason** — every pixel a fill
value, every hour null. It is never zero, and a change is never computed
across it (D-221).

Reference: docs/DECISIONS.md D-133, D-221, D-229.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from meridian.datasets.environment_rows import EnvironmentSample
from meridian.regions.geometry import Polygon
from meridian.regions.rows import Record

__all__ = [
    "CELLS",
    "METHOD_VERSION",
    "PIXELS",
    "SeriesPoint",
    "area_series",
    "latest_revisions",
]

METHOD_VERSION = "regions-1"
PIXELS = ("ndvi", "night_lights_radiance")
CELLS = ("precipitation", "aerosol_optical_depth", "cloud_cover")
FIRE = "fire_radiative_power"
FIRE_SOURCE = "nasa_firms"
DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One period's value for one area, and everything it was computed from."""

    area_id: int
    quantity: str
    period_from: datetime
    period_until: datetime
    value: float | None
    missing_reason: str | None
    count: int
    """How many published values the point combines."""

    unit: str
    method: str
    sources: tuple[str, ...]
    products: tuple[str, ...]
    record_ids: tuple[int, ...]
    retrieved_at: datetime
    """When the newest artefact behind the point was fetched."""


def latest_revisions(
    samples: Iterable[EnvironmentSample],
) -> tuple[EnvironmentSample, ...]:
    """One sample per value: the one published last.

    A value is its source, its ``series_key`` and its place. The place is part
    of it because several adapters key a value by time alone (an hour of cloud,
    a day of rain, a composite's pixel) and are asked about several points, so
    two places' values share a ``series_key`` without one revising the other.
    """
    latest: dict[tuple[str, str, float | None, float | None], EnvironmentSample] = {}
    for one in samples:
        key = (one.source_id, one.series_key, one.lat_deg, one.lon_deg)
        held = latest.get(key)
        if held is None or (one.published_at, one.sample_id) > (
            held.published_at,
            held.sample_id,
        ):
            latest[key] = one
    return tuple(sorted(latest.values(), key=lambda one: one.sample_id))


def area_series(
    area_id: int,
    polygon: Polygon,
    samples: Sequence[EnvironmentSample],
    records: Mapping[int, Record],
    nearest_km: float,
) -> list[SeriesPoint]:
    """Every series the snapshot supports for one area, in a stable order.

    Args:
        area_id: The area.
        polygon: Its shape.
        samples: The snapshot's values, latest revisions only.
        records: The snapshot's artefacts, by id.
        nearest_km: How far a cell may be and still describe the area.

    Returns:
        Points ordered by quantity, then period.
    """
    points: list[SeriesPoint] = []
    by_quantity: dict[str, list[EnvironmentSample]] = defaultdict(list)
    for one in samples:
        by_quantity[one.quantity].append(one)
    context = _Context(area_id, polygon, records)
    for quantity in PIXELS:
        points.extend(_pixels(context, by_quantity.get(quantity, [])))
    for quantity in CELLS:
        points.extend(_cells(context, by_quantity.get(quantity, []), nearest_km))
    points.extend(_fires(context, by_quantity.get(FIRE, [])))
    return sorted(points, key=lambda one: (one.quantity, one.period_from))


@dataclass(frozen=True, slots=True)
class _Context:
    area_id: int
    polygon: Polygon
    records: Mapping[int, Record]


def _pixels(context: _Context, samples: list[EnvironmentSample]) -> list[SeriesPoint]:
    inside = [one for one in samples if _inside(context.polygon, one)]
    how = "mean of pixels whose centre is inside the area"
    if not inside:
        inside = [one for one in samples if _reaches(context.polygon, one)]
        how = "mean of pixels whose footprint reaches the area"
    groups: dict[tuple[datetime, datetime], list[EnvironmentSample]] = defaultdict(list)
    for one in inside:
        groups[(one.observed_from, one.observed_to)].append(one)
    return [
        _point(context, members, span, how, "every pixel was missing")
        for span, members in sorted(groups.items())
    ]


def _cells(
    context: _Context, samples: list[EnvironmentSample], nearest_km: float
) -> list[SeriesPoint]:
    located = [(one, _place(one)) for one in samples if _place(one) is not None]
    inside = [one for one, _ in located if _inside(context.polygon, one)]
    how = "daily mean of model cells inside the area"
    if not inside and located:
        distance, place = min(
            (context.polygon.distance_km(*where), where)
            for _, where in located
            if where is not None
        )
        if distance > nearest_km:
            return []
        inside = [one for one, where in located if where == place]
        how = f"daily mean of the nearest model cell, {distance:.1f} km from the area"
    groups: dict[tuple[datetime, datetime], list[EnvironmentSample]] = defaultdict(list)
    for one in inside:
        start = _day(one.observed_from)
        groups[(start, start + DAY)].append(one)
    return [
        _point(context, members, span, how, "every value that day was missing")
        for span, members in sorted(groups.items())
    ]


def _fires(context: _Context, samples: list[EnvironmentSample]) -> list[SeriesPoint]:
    days = _covered_days(context)
    inside = [one for one in samples if _inside(context.polygon, one)]
    points = []
    for day, record_ids in sorted(days.items()):
        that_day = [one for one in inside if _day(one.observed_from) == day]
        records = [context.records[one] for one in sorted(record_ids)]
        powers = [one.value for one in that_day if one.value is not None]
        common = {
            "area_id": context.area_id,
            "period_from": day,
            "period_until": day + DAY,
            "missing_reason": None,
            "count": len(that_day),
            "sources": (FIRE_SOURCE,),
            "products": tuple(sorted({one.product for one in that_day}))
            or ("VIIRS_SNPP_NRT",),
            "record_ids": tuple(sorted(record_ids)),
            "retrieved_at": max(one.retrieved_at for one in records),
        }
        points.append(
            SeriesPoint(
                quantity="fire_count",
                value=float(len(that_day)),
                unit="detections per day",
                method=f"{METHOD_VERSION}: detections inside the area on a covered day",
                **common,  # type: ignore[arg-type]
            )
        )
        points.append(
            SeriesPoint(
                quantity="fire_radiative_power_sum",
                value=round(sum(powers), 6),
                unit="MW",
                method=f"{METHOD_VERSION}: summed power of detections with one",
                **common,  # type: ignore[arg-type]
            )
        )
    return points


def _covered_days(context: _Context) -> dict[datetime, set[int]]:
    """UTC days a fetched FIRMS artefact covered, over the whole area.

    Only days that had ended when the artefact was fetched: ``follow`` asks for
    today every few hours, and a count of the morning is not a count of the
    day. A day asked about only while it was under way is absent, not 0.
    """
    west, south, east, north = context.polygon.bbox
    days: dict[datetime, set[int]] = defaultdict(set)
    for record in context.records.values():
        if (
            record.source_id != FIRE_SOURCE
            or record.payload_kind != "data"
            or record.extent is None
            or record.valid_from is None
            or record.valid_to is None
        ):
            continue
        box_w, box_s, box_e, box_n = record.extent
        if box_w <= west and box_s <= south and box_e >= east and box_n >= north:
            day = _day(record.valid_from)
            while day < record.valid_to and day + DAY <= record.retrieved_at:
                days[day].add(record.record_id)
                day += DAY
    return days


def _point(
    context: _Context,
    members: list[EnvironmentSample],
    span: tuple[datetime, datetime],
    how: str,
    all_missing: str,
) -> SeriesPoint:
    values = [one.value for one in members if one.value is not None]
    records = [
        context.records[one.record_id]
        for one in members
        if one.record_id in context.records
    ]
    return SeriesPoint(
        area_id=context.area_id,
        quantity=members[0].quantity,
        period_from=span[0],
        period_until=span[1],
        value=round(sum(values) / len(values), 6) if values else None,
        missing_reason=None if values else all_missing,
        count=len(values),
        unit=members[0].value_unit,
        method=f"{METHOD_VERSION}: {how}",
        sources=tuple(sorted({one.source_id for one in members})),
        products=tuple(sorted({one.product for one in members})),
        record_ids=tuple(sorted({one.record_id for one in members})),
        retrieved_at=max(one.retrieved_at for one in records)
        if records
        else max(one.published_at for one in members),
    )


def _place(one: EnvironmentSample) -> tuple[float, float] | None:
    if one.lat_deg is None or one.lon_deg is None:
        return None
    return one.lat_deg, one.lon_deg


def _inside(polygon: Polygon, one: EnvironmentSample) -> bool:
    return (
        one.lat_deg is not None
        and one.lon_deg is not None
        and polygon.contains(one.lat_deg, one.lon_deg)
    )


def _reaches(polygon: Polygon, one: EnvironmentSample) -> bool:
    if one.lat_deg is None or one.lon_deg is None or one.footprint_m is None:
        return False
    return polygon.distance_km(one.lat_deg, one.lon_deg) <= one.footprint_m / 2000.0


def _day(moment: datetime) -> datetime:
    utc = moment.astimezone(UTC)
    return datetime(utc.year, utc.month, utc.day, tzinfo=UTC)

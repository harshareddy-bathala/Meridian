"""A regional report: every area's series, change, coverage and cross-check, from files.

:func:`build_report` is a pure function of a raw snapshot's files and a
configuration — no database, no network, no clock — so the same two inputs
always give the same report, byte for byte, and its hash (rule 8, D-229).
:func:`report_files` renders it as JSON Lines, one table per file, which
:mod:`meridian.regions.publish` seals in a content-addressed directory.

Retired areas are listed and nothing is computed for them: a series that
stopped when an area was retired is still in every earlier report.

Reference: docs/DECISIONS.md D-229 to D-233.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.canonical import canonical_line
from meridian.regions.change import Change, measure_change
from meridian.regions.config import RegionsConfig
from meridian.regions.coverage import Covering, covering_receptions
from meridian.regions.crosscheck import (
    IngestAgreement,
    WeatherAgreement,
    ingest_agreement,
    weather_agreement,
)
from meridian.regions.rows import Area, RegionRows, read_region_rows
from meridian.regions.series import SeriesPoint, area_series, latest_revisions

__all__ = [
    "IMAGERY_LABEL",
    "ImageryRef",
    "RegionsReport",
    "alert_id",
    "build_report",
    "report_files",
]

IMAGERY_LABEL = "imagery — displayed for context only; no value is read from it"
"""What every tile beside an area is called, wherever it is shown (D-133)."""


@dataclass(frozen=True, slots=True)
class ImageryRef:
    """A tile that may be shown behind an area, as a reference and never a number."""

    area_id: int
    record_id: int
    source_id: str
    original_identifier: str
    retrieved_at: datetime
    valid_from: datetime | None


@dataclass(frozen=True, slots=True)
class RegionsReport:
    """Everything one report says, before it is rendered."""

    areas: tuple[Area, ...]
    series: tuple[SeriesPoint, ...]
    changes: tuple[Change, ...]
    coverage: tuple[Covering, ...]
    ingest: tuple[IngestAgreement, ...]
    weather: tuple[WeatherAgreement, ...]
    imagery: tuple[ImageryRef, ...]
    include_simulated: bool


def build_report(files: Mapping[str, bytes], config: RegionsConfig) -> RegionsReport:
    """Compute a report from a raw snapshot's files.

    Args:
        files: The raw snapshot's files, read and verified.
        config: Periods, rules, bootstrap and thresholds.

    Returns:
        The report, every table in a stable order.
    """
    rows = read_region_rows(files)
    samples = latest_revisions(rows.samples)
    series: list[SeriesPoint] = []
    changes: list[Change] = []
    coverage: list[Covering] = []
    ingest: list[IngestAgreement] = []
    weather: list[WeatherAgreement] = []
    imagery: list[ImageryRef] = []
    for area in rows.areas:
        if not area.active:
            continue
        points = area_series(
            area.area_id, area.polygon, samples, rows.records, config.nearest_km
        )
        series.extend(points)
        changes.extend(_changes(area, points, config))
        covering = covering_receptions(
            area.area_id,
            area.polygon,
            rows.receptions,
            rows.tracks,
            swath_km=config.swath_km,
            include_simulated=config.include_simulated,
        )
        coverage.extend(covering)
        ingest.extend(ingest_agreement(area.area_id, covering, points))
        weather.append(_weather(area, rows, points, config))
        imagery.extend(_imagery(area, rows))
    return RegionsReport(
        areas=rows.areas,
        series=tuple(series),
        changes=tuple(changes),
        coverage=tuple(coverage),
        ingest=tuple(ingest),
        weather=tuple(weather),
        imagery=tuple(imagery),
        include_simulated=config.include_simulated,
    )


def alert_id(change: Change, lineage: bytes) -> str:
    """The id an alert is recorded under: what it is about, and what it came from.

    Args:
        change: A change whose verdict is ``alert``.
        lineage: The raw snapshot's and the configuration's hashes, joined.
    """
    about = (
        f"{change.area_id}|{change.quantity}|{change.rule.kind}|"
        f"{change.rule.threshold}|{change.baseline.start.isoformat()}|"
        f"{change.baseline.until.isoformat()}|{change.current.start.isoformat()}|"
        f"{change.current.until.isoformat()}|{lineage.hex()}"
    )
    return "ra_" + hashlib.sha256(about.encode()).hexdigest()[:24]


def report_files(report: RegionsReport, lineage: bytes) -> dict[str, bytes]:
    """The report as JSON Lines files, one table each.

    Args:
        report: What :func:`build_report` built.
        lineage: The raw snapshot's and configuration's hashes, joined, which
            alert ids are derived from.
    """
    tables: dict[str, list[Mapping[str, object]]] = {
        "areas": [_area_row(one) for one in report.areas],
        "series": [_as_row(one) for one in report.series],
        "changes": [_change_row(one) for one in report.changes],
        "alerts": [
            _change_row(one) | {"alert_id": alert_id(one, lineage)}
            for one in report.changes
            if one.verdict == "alert"
        ],
        "coverage": [_as_row(one) for one in report.coverage],
        "ingest_agreement": [_agreement_row(one) for one in report.ingest],
        "weather_agreement": [_weather_row(one) for one in report.weather],
        "imagery": [_as_row(one) | {"label": IMAGERY_LABEL} for one in report.imagery],
    }
    return {
        f"{name}.jsonl": b"".join(canonical_line(row) for row in rows)
        for name, rows in tables.items()
    }


def _changes(
    area: Area, points: list[SeriesPoint], config: RegionsConfig
) -> list[Change]:
    out = []
    for quantity in sorted({one.quantity for one in points} | set(config.rules)):
        found = measure_change(
            area.area_id,
            quantity,
            [one for one in points if one.quantity == quantity],
            config,
        )
        if found is not None:
            out.append(found)
    return out


def _weather(
    area: Area, rows: RegionRows, points: list[SeriesPoint], config: RegionsConfig
) -> WeatherAgreement:
    return weather_agreement(
        area.area_id,
        area.polygon,
        rows.receptions,
        rows.stations,
        points,
        wet_day_mm=config.wet_day_mm,
        min_points=config.min_points,
    )


def _imagery(area: Area, rows: RegionRows) -> list[ImageryRef]:
    west, south, east, north = area.polygon.bbox
    refs = []
    for record in sorted(rows.records.values(), key=lambda one: one.record_id):
        if record.payload_kind != "tile" or record.extent is None:
            continue
        box_w, box_s, box_e, box_n = record.extent
        if box_e <= west or box_w >= east or box_n <= south or box_s >= north:
            continue
        refs.append(
            ImageryRef(
                area_id=area.area_id,
                record_id=record.record_id,
                source_id=record.source_id,
                original_identifier=record.original_identifier,
                retrieved_at=record.retrieved_at,
                valid_from=record.valid_from,
            )
        )
    return refs


def _as_row(value: object) -> dict[str, object]:
    return {name: getattr(value, name) for name in value.__slots__}  # type: ignore[attr-defined]


def _area_row(area: Area) -> dict[str, object]:
    west, south, east, north = area.polygon.bbox
    return {
        "area_id": area.area_id,
        "label": area.label,
        "area_km2": area.area_km2,
        "active": area.active,
        "bbox": [west, south, east, north],
        "geometry_sha256": area.polygon.sha256,
    }


def _change_row(change: Change) -> dict[str, object]:
    return _as_row(change) | {
        "rule": {"kind": change.rule.kind, "threshold": change.rule.threshold},
        "baseline": {"from": change.baseline.start, "until": change.baseline.until},
        "current": {"from": change.current.start, "until": change.current.until},
    }


def _rate(rate: object) -> dict[str, object] | None:
    return None if rate is None else _as_row(rate)


def _agreement_row(one: IngestAgreement) -> dict[str, object]:
    return _as_row(one) | {"rate": _rate(one.rate)}


def _weather_row(one: WeatherAgreement) -> dict[str, object]:
    return _as_row(one) | {"wet": _rate(one.wet), "dry": _rate(one.dry)}

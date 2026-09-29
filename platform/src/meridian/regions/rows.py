"""A raw snapshot's rows as regional monitoring reads them, typed once.

Everything a regional report says is computed from one raw snapshot: the areas
registered by its instant, the published values and the artefacts they came
from, and our own receptions with the ground beneath each pass (D-229, D-230).
Nothing here reads a database, a network or a clock, so a report is a pure
function of the snapshot, its configuration and a seed (rule 8).

A snapshot exported before Stage 32 has no areas and no ground tracks, and
reads as holding none — a report from it says so rather than failing.

Reference: docs/DECISIONS.md D-143, D-229, D-230.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.environment_rows import (
    EnvironmentSample,
    read_environment_samples,
)
from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    field,
    flag,
    instant,
    integer,
    jsonl_rows,
    number,
    optional_instant,
    text,
)
from meridian.regions.geometry import GeometryError, Polygon

__all__ = [
    "Area",
    "GroundTrack",
    "Reception",
    "Record",
    "RegionRows",
    "read_region_rows",
]


@dataclass(frozen=True, slots=True)
class Area:
    """A registered area as the snapshot holds it. Its notes are never exported."""

    area_id: int
    label: str
    polygon: Polygon
    area_km2: float
    active: bool


@dataclass(frozen=True, slots=True)
class Record:
    """One ingested artefact: where it came from and what it covered."""

    record_id: int
    source_id: str
    original_identifier: str
    payload_kind: str
    retrieved_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    extent: tuple[float, float, float, float] | None
    """``west, south, east, north``, where the artefact recorded one."""


@dataclass(frozen=True, slots=True)
class GroundTrack:
    """The ground beneath one pass, every ``step_s`` from ``start``."""

    pass_id: int
    satellite_id: str
    station_id: str
    simulated: bool
    start: datetime
    step_s: int
    points: tuple[tuple[float, float], ...]
    """``(lat_deg, lon_deg)`` pairs."""


@dataclass(frozen=True, slots=True)
class Reception:
    """One assignment's latest report, with what regional checks need of it."""

    assignment_id: str
    pass_id: int
    station_id: str
    satellite_id: str
    outcome: str
    started_at: datetime
    ended_at: datetime | None
    simulated: bool
    listening_confirmed: bool | None
    """As the registry answered at export (D-145); None where it was not asked."""


@dataclass(frozen=True, slots=True)
class RegionRows:
    """Everything a regional report reads from one raw snapshot."""

    areas: tuple[Area, ...]
    samples: tuple[EnvironmentSample, ...]
    records: Mapping[int, Record]
    tracks: Mapping[int, GroundTrack]
    receptions: tuple[Reception, ...]
    stations: Mapping[str, tuple[float, float]]


def read_region_rows(files: Mapping[str, bytes]) -> RegionRows:
    """Type a raw snapshot's rows for regional monitoring.

    Raises:
        MalformedSnapshotError: A row is the wrong shape, or an area's stored
            shape is not a polygon.
    """
    return RegionRows(
        areas=tuple(_area(one) for one in _lines(files, "areas_of_interest")),
        samples=read_environment_samples(files),
        records={
            one.record_id: one
            for one in (_record(row) for row in _lines(files, "ingest_records"))
        },
        tracks={
            one.pass_id: one
            for one in (_track(row) for row in _lines(files, "pass_ground_tracks"))
        },
        receptions=_receptions(files),
        stations={
            text(one, "station_id"): (number(one, "lat_deg"), number(one, "lon_deg"))
            for one in _lines(files, "stations")
            if one.get("lat_deg") is not None and one.get("lon_deg") is not None
        },
    )


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    """A file's rows, or none for a snapshot made before the file existed."""
    data = files.get(f"{name}.jsonl")
    return [] if data is None else jsonl_rows(data, f"{name}.jsonl")


def _area(row: Mapping[str, object]) -> Area:
    try:
        polygon = Polygon.from_geojson(field(row, "geometry"))
    except GeometryError as exc:
        message = f"area {row.get('area_id')}: {exc}"
        raise MalformedSnapshotError(message) from exc
    return Area(
        area_id=integer(row, "area_id"),
        label=text(row, "label"),
        polygon=polygon,
        area_km2=number(row, "area_km2"),
        active=flag(row, "active"),
    )


def _record(row: Mapping[str, object]) -> Record:
    extent = row.get("spatial_extent")
    box = None
    if isinstance(extent, dict):
        try:
            box = tuple(
                float(extent[side]) for side in ("west", "south", "east", "north")
            )
        except (KeyError, TypeError, ValueError):
            box = None
    return Record(
        record_id=integer(row, "record_id"),
        source_id=text(row, "source_id"),
        original_identifier=text(row, "original_identifier"),
        payload_kind=text(row, "payload_kind"),
        retrieved_at=instant(row, "retrieved_at"),
        valid_from=optional_instant(row, "valid_from"),
        valid_to=optional_instant(row, "valid_to"),
        extent=box,  # type: ignore[arg-type]
    )


def _track(row: Mapping[str, object]) -> GroundTrack:
    lats, lons = field(row, "lat_deg"), field(row, "lon_deg")
    if (
        not isinstance(lats, list)
        or not isinstance(lons, list)
        or len(lats) != len(lons)
    ):
        message = f"ground track {row.get('pass_id')} has unequal coordinates"
        raise MalformedSnapshotError(message)
    return GroundTrack(
        pass_id=integer(row, "pass_id"),
        satellite_id=text(row, "satellite_id"),
        station_id=text(row, "station_id"),
        simulated=flag(row, "simulated"),
        start=instant(row, "start"),
        step_s=integer(row, "step_s"),
        points=tuple((float(a), float(b)) for a, b in zip(lats, lons, strict=True)),
    )


def _receptions(files: Mapping[str, bytes]) -> tuple[Reception, ...]:
    """Each assignment's latest revision, joined to its pass and its listening."""
    latest: dict[str, Mapping[str, object]] = {}
    for one in _lines(files, "observations"):
        held = latest.get(text(one, "assignment_id"))
        if held is None or integer(one, "revision") > integer(held, "revision"):
            latest[text(one, "assignment_id")] = one
    passes = {
        text(one, "assignment_id"): integer(one, "pass_id")
        for one in _lines(files, "assignments")
    }
    listening = {
        text(one, "assignment_id"): flag(one, "listening_confirmed")
        for one in _lines(files, "listening")
    }
    return tuple(
        Reception(
            assignment_id=assignment_id,
            pass_id=passes[assignment_id],
            station_id=text(one, "station_id"),
            satellite_id=text(one, "satellite_id"),
            outcome=text(one, "outcome"),
            started_at=instant(one, "started_at"),
            ended_at=optional_instant(one, "ended_at"),
            simulated=flag(one, "simulated"),
            listening_confirmed=listening.get(assignment_id),
        )
        for assignment_id, one in sorted(latest.items())
        if assignment_id in passes
    )

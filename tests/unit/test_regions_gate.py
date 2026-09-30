"""Stage 32's completion gate, asserted from a snapshot alone with the network refused.

    An area registered in the demonstration shows its record and its
    coverage, built entirely from snapshots, with every number traceable to
    an ingested record and every image labelled as imagery.

The snapshot is written by hand through the same publisher an export uses: one
area over Bengaluru and one retired; a vegetation index that falls, rain that
does not, fires that appear; a tile; and our own receptions — decoded passes
over the area, one far away, one heard without a decode, and one simulated.

Each clause of the roadmap's test list has a test:

* an area's series is reproducible from a snapshot alone;
* a series states its source, product version and retrieval time for every point;
* coverage counts only receptions whose pass geometry actually crosses the area;
* simulated receptions are excluded from coverage unless asked for by name;
* no tile is read for a value.

Reference: docs/DECISIONS.md D-133, D-227 to D-233.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.publish import read_directory
from meridian.regions.config import Period, RegionsConfig
from meridian.regions.geometry import Polygon
from meridian.regions.publish import publish_report
from meridian.regions.report import IMAGERY_LABEL, build_report
from meridian.regions.report_text import report_lines

AREA = Polygon.from_bbox(77.45, 12.85, 77.75, 13.10)
INSIDE = (12.95, 77.60)
OUTSIDE = (14.00, 77.60)
JUNE = datetime(2026, 6, 1, tzinfo=UTC)
SEPT = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)
RAIN = [0.0, 2.0, 0.0, 3.0, None, 5.0, 0.0, 1.5, 0.0, 0.0]
"""Ten days of precipitation, the fifth a published gap."""

CONFIG = RegionsConfig(
    baseline=Period(JUNE, datetime(2026, 8, 1, tzinfo=UTC)),
    current=Period(SEPT, datetime(2026, 9, 29, tzinfo=UTC)),
    seed=7,
    resamples=400,
)


class World:
    """A raw snapshot's rows, built up one fact at a time."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, object]]] = {}
        self._ids = iter(range(1, 10_000))

    def add(self, table: str, **row: object) -> None:
        self.tables.setdefault(table, []).append(row)

    def record(
        self, source_id: str, day: datetime | None, kind: str = "data", **extra: object
    ) -> int:
        record_id = next(self._ids)
        self.add(
            "ingest_records",
            record_id=record_id,
            source_id=source_id,
            original_identifier=f"{source_id}/{record_id}",
            payload_kind=kind,
            retrieved_at=(day or SEPT) + timedelta(days=2),
            valid_from=day,
            valid_to=None if day is None else day + DAY,
            spatial_extent=extra.get("extent"),
        )
        return record_id

    def sample(  # noqa: PLR0913, PLR0917 — a sample is this wide
        self,
        record_id: int,
        quantity: str,
        start: datetime,
        span: timedelta,
        value: float | None,
        place: tuple[float, float],
    ) -> None:
        sample_id = next(self._ids)
        self.add(
            "environment_samples",
            sample_id=sample_id,
            record_id=record_id,
            source_id=_source(quantity),
            series_key=f"{quantity}:{start.isoformat()}:{place}:{sample_id}",
            quantity=quantity,
            value=value,
            missing_reason=None if value is not None else "fill value",
            value_unit="u",
            observed_from=start,
            observed_to=start + span,
            published_at=start + span,
            product=f"{quantity} v1",
            lat_deg=place[0],
            lon_deg=place[1],
            footprint_m=250.0,
            quality=None,
        )

    def reception(
        self,
        day: datetime,
        outcome: str,
        track_lon: float,
        station: str = "st_a",
        simulated: bool = False,
    ) -> None:
        pass_id = next(self._ids)
        aos = day + timedelta(hours=4)
        self.add("assignments", assignment_id=f"as_{pass_id}", pass_id=pass_id)
        self.add(
            "observations",
            assignment_id=f"as_{pass_id}",
            revision=1,
            station_id=station,
            satellite_id="norad:57166",
            outcome=outcome,
            started_at=aos + timedelta(minutes=1),
            ended_at=aos + timedelta(minutes=10),
            simulated=simulated,
        )
        self.add("listening", assignment_id=f"as_{pass_id}", listening_confirmed=True)
        self.add(
            "pass_ground_tracks",
            pass_id=pass_id,
            satellite_id="norad:57166",
            station_id=station,
            simulated=simulated,
            start=aos,
            step_s=30,
            lat_deg=[round(5.0 + 0.8 * n, 3) for n in range(24)],
            lon_deg=[track_lon] * 24,
        )


def _source(quantity: str) -> str:
    return {
        "ndvi": "ornl_modis_ndvi",
        "precipitation": "nasa_power_precipitation",
        "fire_radiative_power": "nasa_firms",
    }[quantity]


def build_world() -> dict[str, list[dict[str, object]]]:
    world = World()
    world.add(
        "areas_of_interest",
        area_id=1,
        label="Bengaluru urban",
        geometry=AREA.to_geojson(),
        area_km2=AREA.area_km2(),
        active=True,
    )
    retired = Polygon.from_bbox(80.0, 12.0, 80.5, 12.5)
    world.add(
        "areas_of_interest",
        area_id=2,
        label="Retired coast",
        geometry=retired.to_geojson(),
        area_km2=retired.area_km2(),
        active=False,
    )
    world.add("stations", station_id="st_a", lat_deg=12.97, lon_deg=77.59)
    world.add("stations", station_id="st_sim", lat_deg=12.97, lon_deg=77.59)
    _vegetation(world)
    _rain(world)
    _fires(world)
    world.record(
        "nasa_gibs",
        None,
        kind="tile",
        extent={"west": 72.0, "south": 9.0, "east": 81.0, "north": 18.0},
    )
    _receptions(world)
    return world.tables


def _vegetation(world: World) -> None:
    composites = [(JUNE + n * 16 * DAY, 0.61) for n in range(4)]
    composites += [(SEPT + n * 8 * DAY, 0.41) for n in range(3)]
    for start, level in composites:
        record = world.record("ornl_modis_ndvi", start)
        for place, value in (
            (INSIDE, level - 0.01),
            ((13.0, 77.5), level + 0.01),
            (OUTSIDE, 0.99),
        ):
            world.sample(record, "ndvi", start, 16 * DAY, value, place)


def _rain(world: World) -> None:
    for first in (JUNE, SEPT):
        record = world.record("nasa_power_precipitation", first)
        for n, value in enumerate(RAIN):
            world.sample(
                record, "precipitation", first + n * DAY, DAY, value, (12.9, 77.6)
            )


def _fires(world: World) -> None:
    box = {"west": 74.0, "south": 11.5, "east": 78.6, "north": 18.5}
    for first, per_day in ((JUNE, 0), (SEPT, 5)):
        for n in range(5):
            day = first + n * DAY
            record = world.record("nasa_firms", day, extent=box)
            for k in range(per_day):
                world.sample(
                    record,
                    "fire_radiative_power",
                    day + timedelta(hours=8),
                    timedelta(0),
                    4.0 + k,
                    (12.95 + k * 0.01, 77.6),
                )
            world.sample(
                record,
                "fire_radiative_power",
                day + timedelta(hours=8),
                timedelta(0),
                9.0,
                OUTSIDE,
            )


def _receptions(world: World) -> None:
    for n in (0, 1, 2, 3, 4, 5, 6, 19):
        world.reception(SEPT + n * DAY, "decoded", 77.6)
    world.reception(SEPT + 2 * DAY, "decoded", 0.0)
    world.reception(SEPT + 8 * DAY, "no_signal", 77.6)
    world.reception(SEPT + DAY, "decoded", 77.6, station="st_sim", simulated=True)


@pytest.fixture
def snapshot(raw_snapshot: Any) -> Path:
    return raw_snapshot(build_world())


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


# --- reproducible from a snapshot alone --------------------------------------


def test_a_report_is_reproduced_three_times_with_nothing_reachable(
    snapshot: Path, tmp_path: Path, guarded: Any
) -> None:
    published = [
        publish_report(
            snapshot,
            CONFIG,
            root=tmp_path / "datasets",
            created_at=datetime(2026, 9, 29, 12, n, tzinfo=UTC),
        )[0]
        for n in range(3)
    ]
    assert len({one.path for one in published}) == 1
    assert [one.written for one in published] == [True, False, False]
    assert guarded.attempts == []


def test_a_different_seed_is_a_different_report(snapshot: Path, tmp_path: Path) -> None:
    first, _ = publish_report(snapshot, CONFIG, root=tmp_path, created_at=JUNE)
    other, _ = publish_report(
        snapshot, replace(CONFIG, seed=8), root=tmp_path, created_at=JUNE
    )
    assert first.path != other.path
    manifest = read_directory(first.path).manifest
    assert manifest.kind == "regions_report"
    assert manifest.config_sha256 == CONFIG.sha256
    assert manifest.parameters["seed"] == 7


# --- every point cites its inputs --------------------------------------------


def test_every_point_states_its_source_product_and_retrieval(snapshot: Path) -> None:
    files = read_directory(snapshot).files
    report = build_report(files, CONFIG)
    records = {one["record_id"] for one in build_world()["ingest_records"]}
    assert report.series
    for point in report.series:
        assert point.sources and point.products and point.record_ids
        assert set(point.record_ids) <= records
        assert point.retrieved_at is not None
        assert point.method.startswith("regions-1: ")


def test_vegetation_is_the_mean_of_pixels_inside_and_nothing_outside(
    snapshot: Path,
) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    ndvi = [one for one in report.series if one.quantity == "ndvi"]
    assert len(ndvi) == 7
    assert ndvi[0].value == pytest.approx(0.61)
    assert all(one.count == 2 for one in ndvi)


def test_a_published_gap_is_missing_and_never_zero(snapshot: Path) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    gap = [
        one
        for one in report.series
        if one.quantity == "precipitation" and one.value is None
    ]
    assert [one.period_from for one in gap] == [JUNE + 4 * DAY, SEPT + 4 * DAY]
    assert all(one.missing_reason for one in gap)


def test_a_covered_day_without_fires_is_zero_and_an_unasked_day_is_absent(
    snapshot: Path,
) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    counts = {
        one.period_from: one.value
        for one in report.series
        if one.quantity == "fire_count"
    }
    assert counts[JUNE] == 0.0
    assert counts[SEPT] == 5.0
    assert JUNE + 10 * DAY not in counts


# --- change and alerts --------------------------------------------------------


def test_changes_are_judged_by_their_interval(snapshot: Path) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    verdicts = {one.quantity: one for one in report.changes}
    assert verdicts["ndvi"].verdict == "alert"
    assert verdicts["ndvi"].high is not None and verdicts["ndvi"].high < -0.15
    assert verdicts["fire_count"].verdict == "alert"
    assert verdicts["precipitation"].verdict == "within"
    assert verdicts["night_lights_radiance"].verdict == "insufficient"


def test_alerts_carry_derived_ids_in_the_report(snapshot: Path, tmp_path: Path) -> None:
    published, _ = publish_report(snapshot, CONFIG, root=tmp_path, created_at=JUNE)
    alerts = read_directory(published.path).files["alerts.jsonl"].decode().splitlines()
    assert len(alerts) == 2
    assert all('"alert_id":"ra_' in line for line in alerts)


# --- coverage -----------------------------------------------------------------


def test_coverage_counts_only_decoded_passes_whose_track_crosses(
    snapshot: Path,
) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    covering = [one for one in report.coverage if one.area_id == 1]
    assert len(covering) == 8
    assert all(one.closest_km == 0.0 for one in covering)
    assert not any(one.simulated for one in covering)


def test_a_simulated_reception_counts_only_when_asked_for_by_name(
    snapshot: Path,
) -> None:
    files = read_directory(snapshot).files
    asked = build_report(files, replace(CONFIG, include_simulated=True))
    simulated = [one for one in asked.coverage if one.simulated]
    assert len(simulated) == 1
    assert len(asked.coverage) == 9
    lines = report_lines(asked)
    assert any("SIMULATED" in line for line in lines)


def test_a_retired_area_is_listed_and_nothing_is_computed_for_it(
    snapshot: Path,
) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    assert [one.area_id for one in report.areas] == [1, 2]
    assert {one.area_id for one in report.series} == {1}
    assert "retired — nothing computed" in "\n".join(report_lines(report))


# --- cross-checks ---------------------------------------------------------------


def test_the_ingest_check_lists_the_days_we_imaged_with_no_public_value(
    snapshot: Path,
) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    rain = next(one for one in report.ingest if one.quantity == "precipitation")
    assert rain.days == 8
    assert rain.missing_days == ("2026-09-05", "2026-09-20")
    assert rain.rate is not None and rain.rate.estimate == pytest.approx(6 / 8)


def test_the_chain_check_compares_wet_and_dry_days(snapshot: Path) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    weather = report.weather[0]
    assert (weather.wet_attempts, weather.dry_attempts) == (3, 5)
    assert weather.unknown_days == 2
    assert weather.verdict == "consistent"


# --- imagery is never a value -------------------------------------------------------


def test_a_tile_is_listed_as_imagery_and_no_point_cites_it(snapshot: Path) -> None:
    report = build_report(read_directory(snapshot).files, CONFIG)
    tiles = {one.record_id for one in report.imagery}
    assert len(tiles) == 1
    assert not any(tiles & set(one.record_ids) for one in report.series)
    assert IMAGERY_LABEL in "\n".join(report_lines(report))

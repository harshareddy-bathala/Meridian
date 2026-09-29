"""The edges of a regional series and its change, one clause at a time.

Reference: docs/DECISIONS.md D-221, D-229, D-231.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from meridian.datasets.environment_rows import EnvironmentSample
from meridian.regions import change
from meridian.regions.config import Period, RegionsConfig, Rule
from meridian.regions.geometry import Polygon
from meridian.regions.rows import Record
from meridian.regions.series import SeriesPoint, area_series, latest_revisions

AREA = Polygon.from_bbox(77.45, 12.85, 77.75, 13.10)
DAY = timedelta(days=1)
MONDAY = datetime(2026, 9, 28, tzinfo=UTC)


def a_sample(
    sample_id: int, place: tuple[float, float], value: float
) -> EnvironmentSample:
    """An hour of cloud keyed by time alone, as Open-Meteo's adapter keys it."""
    return EnvironmentSample(
        sample_id=sample_id,
        record_id=sample_id,
        source_id="open_meteo_cloud",
        series_key="cloud_cover:2026-09-28T10:00:00+00:00",
        quantity="cloud_cover",
        value=value,
        missing_reason=None,
        value_unit="%",
        observed_from=MONDAY + timedelta(hours=10),
        observed_to=MONDAY + timedelta(hours=11),
        published_at=MONDAY + timedelta(hours=sample_id),
        product="best_match",
        lat_deg=place[0],
        lon_deg=place[1],
        footprint_m=None,
        quality=None,
    )


def test_two_places_sharing_a_series_key_are_two_values() -> None:
    bengaluru, chennai = (12.95, 77.60), (13.08, 80.27)
    held = latest_revisions([a_sample(1, bengaluru, 40.0), a_sample(2, chennai, 90.0)])
    assert [(one.lat_deg, one.value) for one in held] == [(12.95, 40.0), (13.08, 90.0)]


def test_a_revision_at_one_place_still_replaces_the_value_there() -> None:
    here = (12.95, 77.60)
    held = latest_revisions([a_sample(1, here, 40.0), a_sample(2, here, 55.0)])
    assert [one.value for one in held] == [55.0]


def a_fires_record(retrieved_at: datetime) -> Record:
    return Record(
        record_id=7,
        source_id="nasa_firms",
        original_identifier="nasa_firms/7",
        payload_kind="data",
        retrieved_at=retrieved_at,
        valid_from=MONDAY,
        valid_to=MONDAY + DAY,
        extent=(77.0, 12.5, 78.0, 13.5),
    )


def fire_days(retrieved_at: datetime) -> list[datetime]:
    records = {7: a_fires_record(retrieved_at)}
    points = area_series(1, AREA, [], records, nearest_km=50.0)
    return [one.period_from for one in points if one.quantity == "fire_count"]


def test_a_day_fetched_while_under_way_is_absent_not_zero() -> None:
    assert fire_days(MONDAY + timedelta(hours=9)) == []


def test_a_day_fetched_after_it_ended_is_covered() -> None:
    assert fire_days(MONDAY + DAY + timedelta(hours=3)) == [MONDAY]


def points(values: list[float], start: datetime) -> list[SeriesPoint]:
    return [
        SeriesPoint(
            area_id=1,
            quantity="ndvi",
            period_from=start + index * DAY,
            period_until=start + (index + 1) * DAY,
            value=value,
            missing_reason=None,
            count=1,
            unit="1",
            method="test",
            sources=("s",),
            products=("p",),
            record_ids=(index,),
            retrieved_at=start,
        )
        for index, value in enumerate(values)
    ]


class FirstEveryTime:
    """A generator that always draws the first element: a zero, here."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def choice(self, values: Sequence[float]) -> float:
        return values[0]


def test_a_baseline_that_resamples_to_zero_every_time_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A baseline whose mean is above zero, but whose every resample is zero."""
    monkeypatch.setattr(change, "random", SimpleNamespace(Random=FirstEveryTime))
    june, sept = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
    config = RegionsConfig(
        baseline=Period(june, june + 30 * DAY),
        current=Period(sept, sept + 30 * DAY),
        rules={"ndvi": Rule("relative", -0.15)},
        resamples=100,
    )
    series = points([0.0, 0.0, 0.1], june) + points([0.5] * 3, sept)
    found = change.measure_change(1, "ndvi", series, config)
    assert found is not None
    assert (found.verdict, found.change) == ("insufficient", None)
    assert found.reason == "every resampled baseline was zero"

"""``meridian.datasets.propensity`` — D-152's binned estimate, case by case.

Reference: docs/DECISIONS.md D-152.
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meridian.datasets.propensity import (
    LEVELS,
    BinnedPropensity,
    Candidate,
    PropensityModel,
)
from meridian.datasets.selection_config import PropensityConfig

NOON_UTC = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
MODEL = BinnedPropensity(PropensityConfig(min_cell=4))


def candidate(
    *,
    attempted: bool = True,
    satellite: str = "norad:57166",
    peak: float = 45.0,
    at: datetime = NOON_UTC,
    longitude: float | None = 0.0,
) -> Candidate:
    return Candidate(
        population="own",
        station="st_a",
        satellite_id=satellite,
        aos=at,
        max_elevation_deg=peak,
        longitude_deg=longitude,
        attempted=attempted,
    )


def cell_of(taken: int, total: int, **fields: object) -> list[Candidate]:
    """``total`` candidates alike, the first ``taken`` of them attempted."""
    return [
        candidate(attempted=n < taken, **fields)  # type: ignore[arg-type]
        for n in range(total)
    ]


# --- the estimate -------------------------------------------------------------


def test_a_full_cell_is_its_own_ratio() -> None:
    estimates = MODEL.estimate(cell_of(3, 4))

    assert {one.level for one in estimates} == {LEVELS[0]}
    assert {one.propensity for one in estimates} == {0.75}
    assert estimates[0].available == 4


def test_a_sparse_cell_drops_the_hour_first() -> None:
    """Two passes at noon, two at midnight: neither hour cell reaches four."""
    passes = cell_of(2, 2) + cell_of(0, 2, at=NOON_UTC + timedelta(hours=12))

    estimates = MODEL.estimate(passes)

    assert {one.level for one in estimates} == {LEVELS[1]}
    assert {one.propensity for one in estimates} == {0.5}


def test_then_the_satellite() -> None:
    passes = cell_of(1, 2) + cell_of(1, 2, satellite="norad:40069")

    estimates = MODEL.estimate(passes)

    assert {one.level for one in estimates} == {LEVELS[2]}
    assert {one.available for one in estimates} == {4}


def test_the_coarsest_level_is_used_however_sparse() -> None:
    (estimate,) = MODEL.estimate([candidate()])

    assert (estimate.level, estimate.available, estimate.propensity) == (
        LEVELS[2],
        1,
        1.0,
    )


def test_a_station_with_no_longitude_skips_the_hour_level() -> None:
    estimates = MODEL.estimate(cell_of(4, 4, longitude=None))

    assert {one.level for one in estimates} == {LEVELS[1]}


def test_every_estimate_names_the_cell_behind_it() -> None:
    (estimate,) = BinnedPropensity(PropensityConfig(min_cell=1)).estimate(
        [candidate(peak=45.0, at=NOON_UTC, longitude=0.0)]
    )

    assert estimate.cell == ("own", "st_a", "norad:57166", "el 30-60", "lst 12-16")


def test_the_two_populations_are_never_one_cell() -> None:
    own = cell_of(4, 4)
    archive = [replace(one, population="archive", attempted=False) for one in own]

    estimates = MODEL.estimate(own + archive)

    assert [one.propensity for one in estimates] == [1.0] * 4 + [0.0] * 4


# --- the bands ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("peak", "band"),
    [
        (0.0, "el 0-15"),
        (14.99, "el 0-15"),
        (15.0, "el 15-30"),
        (59.9, "el 30-60"),
        (60.0, "el 60-90"),
        (90.0, "el 60-90"),
    ],
)
def test_an_edge_belongs_to_the_band_above_it(peak: float, band: str) -> None:
    (estimate,) = BinnedPropensity(PropensityConfig(min_cell=1)).estimate(
        [candidate(peak=peak)]
    )

    assert estimate.cell[3] == band


def test_no_edges_is_one_band() -> None:
    model = BinnedPropensity(PropensityConfig(min_cell=1, elevation_bands_deg=()))

    (estimate,) = model.estimate([candidate(peak=3.0)])

    assert estimate.cell[3] == "el 0-90"


@pytest.mark.parametrize(
    ("utc", "longitude", "band"),
    [
        (NOON_UTC, 0.0, "lst 12-16"),
        (NOON_UTC, 77.6, "lst 16-20"),
        (NOON_UTC, -120.0, "lst 04-08"),
        (NOON_UTC + timedelta(hours=10), 77.6, "lst 00-04"),
        (NOON_UTC - timedelta(hours=12), -1.0, "lst 20-24"),
    ],
    ids=["greenwich", "bengaluru", "california", "past-midnight", "before-midnight"],
)
def test_local_solar_hour_moves_with_longitude(
    utc: datetime, longitude: float, band: str
) -> None:
    """Bengaluru at 12:00 UTC is 17:10 by the sun; four minutes a degree."""
    (estimate,) = BinnedPropensity(PropensityConfig(min_cell=1)).estimate(
        [candidate(at=utc, longitude=longitude)]
    )

    assert estimate.cell[4] == band


# --- what it may not see --------------------------------------------------------


def test_a_candidate_has_no_field_an_outcome_could_arrive_in() -> None:
    """D-152 by construction: the estimator's only input is this type."""
    fields = {one.name for one in dataclasses.fields(Candidate)}

    assert fields == {
        "population",
        "station",
        "satellite_id",
        "aos",
        "max_elevation_deg",
        "longitude_deg",
        "attempted",
    }


def test_estimates_come_back_in_the_order_given() -> None:
    passes = cell_of(2, 3) + cell_of(1, 3, peak=10.0)
    shuffled = random.Random(3).sample(passes, len(passes))

    estimates = MODEL.estimate(shuffled)

    assert [one.candidate for one in estimates] == shuffled


def test_the_order_given_does_not_change_any_estimate() -> None:
    passes = cell_of(2, 5) + cell_of(1, 3, peak=10.0)
    shuffled = random.Random(5).sample(passes, len(passes))

    by_input = {id(one.candidate): one for one in MODEL.estimate(passes)}
    again = {id(one.candidate): one for one in MODEL.estimate(shuffled)}

    assert by_input == again


def test_the_binned_model_is_a_propensity_model() -> None:
    model: PropensityModel = MODEL

    assert model.name == "binned-1"

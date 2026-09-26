"""The calibration report's arithmetic — hand-computed, bin by bin.

``EVALUATION.md`` §7 says every model ships with a reliability diagram, a Brier
score against a base rate and calibration by segment. These tests pin each to
numbers worked out by hand, so a report that looks right for the wrong reason
fails here first.

Reference: docs/DECISIONS.md D-161, D-164.
"""

from __future__ import annotations

import pytest

from meridian.prediction.calibration import (
    BINS,
    DIMENSIONS,
    UNKNOWN,
    Scored,
    age_bucket,
    brier,
    calibrate,
)


def scored(probability: float, positive: bool, **where: str) -> Scored:
    """A scored pass; ``where`` sets ``station``, ``band``, ``age`` or ``path``."""
    return Scored(
        probability=probability,
        positive=positive,
        path=where.get("path", "configured"),
        segments={
            "station": where.get("station", "st_a"),
            "band": where.get("band", "vhf"),
            "element_set_age": where.get("age", "<24 h"),
        },
    )


FOUR = [
    scored(0.8, True),
    scored(0.3, False),
    scored(0.6, False),
    scored(0.1, True),
]
"""Brier (0.04 + 0.09 + 0.36 + 0.81) / 4 = 0.325."""


# --- Brier and skill ----------------------------------------------------------------


def test_brier_is_the_mean_squared_gap() -> None:
    assert brier([(0.8, True), (0.3, False), (0.6, False), (0.1, True)]) == (
        pytest.approx(0.325)
    )


def test_skill_is_measured_against_the_base_rate_given() -> None:
    result = calibrate(FOUR, base_rate=0.5)

    assert result.n == 4
    assert result.decoded == 2
    assert result.brier == pytest.approx(0.325)
    assert result.base_brier == pytest.approx(0.25)
    assert result.skill == pytest.approx(1 - 0.325 / 0.25)


def test_a_constant_prediction_at_the_base_rate_has_no_skill() -> None:
    outcomes = [True, False, False, True, False]

    result = calibrate([scored(0.3, one) for one in outcomes], base_rate=0.3)

    assert result.skill == pytest.approx(0.0)


def test_a_perfect_prediction_has_all_the_skill() -> None:
    outcomes = [True, False, True]

    result = calibrate([scored(float(one), one) for one in outcomes], base_rate=0.5)

    assert result.brier == 0.0
    assert result.skill == pytest.approx(1.0)


def test_the_base_rate_is_the_one_handed_in_never_the_judged_spans() -> None:
    """The judged span decodes 2 of 4; the reference uses what training said."""
    result = calibrate(FOUR, base_rate=0.9)

    assert result.base_rate == 0.9
    assert result.base_brier == pytest.approx((0.01 + 0.81 + 0.81 + 0.01) / 4)


def test_skill_is_undefined_when_the_base_rate_is_already_perfect() -> None:
    result = calibrate([scored(0.7, True), scored(0.9, True)], base_rate=1.0)

    assert result.base_brier == 0.0
    assert result.skill is None


@pytest.mark.parametrize(
    ("scored_passes", "base_rate", "refusal"),
    [
        ([], 0.5, "at least one scored pass"),
        (FOUR, 1.5, "not a probability"),
        (FOUR, -0.1, "not a probability"),
    ],
)
def test_what_cannot_be_calibrated_is_refused(
    scored_passes: list[Scored], base_rate: float, refusal: str
) -> None:
    with pytest.raises(ValueError, match=refusal):
        calibrate(scored_passes, base_rate=base_rate)


def test_an_empty_brier_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one scored pass"):
        brier([])


# --- the reliability diagram ------------------------------------------------------


def test_there_are_ten_bins_and_an_empty_one_is_kept() -> None:
    result = calibrate(FOUR, base_rate=0.5)

    assert len(result.bins) == BINS
    assert [one.n for one in result.bins] == [0, 1, 0, 1, 0, 0, 1, 0, 1, 0]
    empty = result.bins[0]
    assert empty.observed is None
    assert empty.mean_predicted is None
    assert (empty.low, empty.high) == (0.0, 0.1)


@pytest.mark.parametrize(
    ("probability", "index"),
    [(0.0, 0), (0.099, 0), (0.1, 1), (0.3, 3), (0.7, 7), (0.95, 9), (1.0, 9)],
)
def test_a_probability_lands_in_the_bin_whose_low_edge_it_reaches(
    probability: float, index: int
) -> None:
    result = calibrate([scored(probability, True)], base_rate=0.5)

    assert [one.n for one in result.bins].index(1) == index


def test_a_bin_reports_its_mean_prediction_and_observed_frequency() -> None:
    members = [scored(0.62, True), scored(0.64, False), scored(0.66, True)]

    result = calibrate(members, base_rate=0.5)
    bar = result.bins[6]

    assert bar.n == 3
    assert bar.mean_predicted == pytest.approx(0.64)
    assert bar.observed is not None
    assert bar.observed.estimate == pytest.approx(2 / 3)
    assert bar.observed.n == 3
    assert 0.0 <= bar.observed.low < 2 / 3 < bar.observed.high <= 1.0


def test_the_observed_frequency_carries_the_textbook_wilson_interval() -> None:
    """50 of 100: 0.4038 to 0.5962."""
    members = [scored(0.55, n < 50) for n in range(100)]

    bar = calibrate(members, base_rate=0.5).bins[5]

    assert bar.observed is not None
    assert bar.observed.low == pytest.approx(0.4038, abs=1e-4)
    assert bar.observed.high == pytest.approx(0.5962, abs=1e-4)


# --- segments and routes ----------------------------------------------------------


def test_every_pass_is_counted_once_in_each_dimension() -> None:
    members = [
        scored(0.8, True, station="st_b", band="uhf", age="≥168 h"),
        scored(0.2, False, station="st_a", band="vhf", age="<24 h"),
        scored(0.4, True, station="st_a", band="vhf", age=UNKNOWN),
    ]

    result = calibrate(members, base_rate=0.5)
    keys = [(one.dimension, one.value, one.n) for one in result.segments]

    assert keys == [
        ("station", "st_a", 2),
        ("station", "st_b", 1),
        ("band", "uhf", 1),
        ("band", "vhf", 2),
        ("element_set_age", "<24 h", 1),
        ("element_set_age", "≥168 h", 1),
        ("element_set_age", UNKNOWN, 1),
    ]
    for dimension in DIMENSIONS:
        assert sum(one.n for one in result.segments if one.dimension == dimension) == 3


def test_ages_run_youngest_first_with_unknown_last() -> None:
    ages = [UNKNOWN, "≥168 h", "72–168 h", "24–72 h", "<24 h"]

    result = calibrate([scored(0.5, True, age=one) for one in ages], base_rate=0.5)

    assert [
        one.value for one in result.segments if one.dimension == "element_set_age"
    ] == ["<24 h", "24–72 h", "72–168 h", "≥168 h", UNKNOWN]


def test_a_segment_carries_its_own_brier_and_frequency() -> None:
    members = [
        scored(0.8, True, station="st_b"),
        scored(0.2, False, station="st_a"),
        scored(0.4, True, station="st_a"),
    ]

    result = calibrate(members, base_rate=0.5)
    (st_a,) = [one for one in result.segments if one.value == "st_a"]

    assert st_a.brier == pytest.approx((0.04 + 0.36) / 2)
    assert st_a.mean_predicted == pytest.approx(0.3)
    assert st_a.observed.estimate == pytest.approx(0.5)


def test_a_pass_missing_a_dimension_is_counted_as_unknown() -> None:
    bare = Scored(probability=0.5, positive=True, path="configured", segments={})

    result = calibrate([bare], base_rate=0.5)

    assert {one.value for one in result.segments} == {UNKNOWN}
    assert len(result.segments) == len(DIMENSIONS)


def test_routes_are_counted_with_their_own_brier() -> None:
    members = [
        scored(0.9, True, path="configured"),
        scored(0.4, False, path="geometry_fallback"),
        scored(0.6, False, path="geometry_fallback"),
    ]

    result = calibrate(members, base_rate=0.5)

    assert [(one.path, one.n) for one in result.routes] == [
        ("configured", 1),
        ("geometry_fallback", 2),
    ]
    assert result.routes[1].brier == pytest.approx((0.16 + 0.36) / 2)


# --- element-set age -------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "bucket"),
    [
        (None, UNKNOWN),
        (0.0, "<24 h"),
        (23.9, "<24 h"),
        (24.0, "24–72 h"),
        (71.9, "24–72 h"),
        (72.0, "72–168 h"),
        (168.0, "≥168 h"),
        (900.0, "≥168 h"),
    ],
)
def test_element_set_age_falls_in_its_bucket(hours: float | None, bucket: str) -> None:
    assert age_bucket(hours) == bucket

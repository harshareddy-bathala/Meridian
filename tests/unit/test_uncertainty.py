"""``timing_uncertainty_at_age`` — the Phase 1 prior, and the claims it makes.

The value itself is a published prior rather than a measurement, so there is no
external answer to check it against. What can be checked, and is checked here,
are the properties the rest of the platform relies on: that it is never zero,
that it grows with element-set age and never shrinks, that it says which model
produced it, and that the arithmetic matches the model stated in the module
docstring rather than some other model that happens to give similar numbers.

Marked as a unit test by living in ``tests/unit``: no database and no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet
from meridian.orbit.uncertainty import (
    ALONG_TRACK_SPEED_KM_S,
    ERROR_GROWTH_KM_PER_DAY,
    POSITION_ERROR_AT_EPOCH_KM,
    SECONDS_PER_DAY,
    TIMING_PRIOR_METHOD,
    timing_uncertainty_at_age,
)

SIGMA_AT_EPOCH_S = POSITION_ERROR_AT_EPOCH_KM / ALONG_TRACK_SPEED_KM_S
"""1.0 km / 7.5 km/s = 0.133 s — the model with age set to zero."""

SIGMA_AT_ONE_DAY_S = (
    POSITION_ERROR_AT_EPOCH_KM + ERROR_GROWTH_KM_PER_DAY
) / ALONG_TRACK_SPEED_KM_S
"""(1.0 + 3.0) km / 7.5 km/s = 0.533 s."""


def test_the_figure_at_epoch_is_the_fit_residual_not_zero() -> None:
    """``service.py`` calls a zero here actively misleading, so it is asserted.

    Zero would claim perfect knowledge of an element set whose accuracy is
    unstated, and a station reading it would open no margin at all.
    """
    at_epoch = timing_uncertainty_at_age(0.0)

    assert at_epoch.sigma_s > 0
    assert at_epoch.sigma_s == pytest.approx(SIGMA_AT_EPOCH_S)


def test_a_day_of_age_adds_the_published_growth_rate() -> None:
    """The arithmetic is the model, not a curve fitted to look similar.

    Written out from the constants rather than from the function, so a change
    that replaced linear growth with something else — a square root, an
    exponential — would fail here even where it happened to agree at epoch.
    """
    after_a_day = timing_uncertainty_at_age(SECONDS_PER_DAY)

    assert after_a_day.sigma_s == pytest.approx(SIGMA_AT_ONE_DAY_S)
    assert after_a_day.sigma_s - SIGMA_AT_EPOCH_S == pytest.approx(
        ERROR_GROWTH_KM_PER_DAY / ALONG_TRACK_SPEED_KM_S
    )


def test_the_figure_grows_with_age_and_never_shrinks() -> None:
    """Element-set age is the project's proxy for prediction error everywhere.

    A prior that ever narrowed as elements got older would contradict the reason
    age is a first-class feature at all.
    """
    ages_s = [0.0, 3600.0, SECONDS_PER_DAY, 3 * SECONDS_PER_DAY, 7 * SECONDS_PER_DAY]
    sigmas_s = [timing_uncertainty_at_age(age).sigma_s for age in ages_s]

    assert sigmas_s == sorted(sigmas_s)
    assert len(set(sigmas_s)) == len(sigmas_s)


def test_predicting_before_the_epoch_is_no_more_certain_than_after() -> None:
    """SGP4 does not improve running backwards, so the magnitude of age is used.

    A backfill computing a pass from an element set issued later would otherwise
    get a *negative* sigma, which is not a smaller uncertainty — it is a
    nonsense value that would widen nothing and be believed.
    """
    backwards = timing_uncertainty_at_age(-2 * SECONDS_PER_DAY)
    forwards = timing_uncertainty_at_age(+2 * SECONDS_PER_DAY)

    assert backwards.sigma_s == pytest.approx(forwards.sigma_s)
    assert backwards.sigma_s > 0


def test_a_week_old_element_set_is_still_only_a_few_seconds_out() -> None:
    """A magnitude check against what the model is supposed to mean.

    Pass predictions from week-old elements are known to be seconds out, not
    minutes and not milliseconds. A result outside this range would mean a unit
    slipped — days for seconds, or metres for kilometres — while the formula
    still read correctly.
    """
    sigma_s = timing_uncertainty_at_age(7 * SECONDS_PER_DAY).sigma_s
    assert 1.0 < sigma_s < 10.0


def test_every_answer_names_the_model_that_produced_it() -> None:
    """SC-3 compares this prior against a fitted model; both must be identifiable.

    A stored prediction with no method is one that cannot be attributed years
    later, and the comparison is the success criterion.
    """
    result = timing_uncertainty_at_age(SECONDS_PER_DAY)

    assert result.method == TIMING_PRIOR_METHOD
    assert result.method.strip() != ""
    assert result.element_set_age_s == SECONDS_PER_DAY


# --- the service wiring -------------------------------------------------------

EPOCH = datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC)


@pytest.fixture
def iss() -> ElementSet:
    """The element set transcribed in ``test_look_angles_reference.py``."""
    return ElementSet(
        satellite_id="norad:25544",
        epoch=EPOCH,
        line1=("1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"),
        line2=("2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"),
        source="manual",
    )


def test_the_service_measures_age_from_the_element_sets_own_epoch(
    iss: ElementSet,
) -> None:
    """Age comes from the element set, not from the clock or the search window."""
    result = SkyfieldOrbitService().timing_uncertainty(iss, EPOCH + timedelta(days=2))

    assert result.element_set_age_s == pytest.approx(2 * SECONDS_PER_DAY)
    assert result.sigma_s == pytest.approx(
        timing_uncertainty_at_age(2 * SECONDS_PER_DAY).sigma_s
    )


def test_a_naive_datetime_is_refused_at_this_boundary(iss: ElementSet) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    with pytest.raises(ValueError, match="naive"):
        SkyfieldOrbitService().timing_uncertainty(iss, EPOCH.replace(tzinfo=None))

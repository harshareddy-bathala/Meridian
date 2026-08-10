"""``element_set_divergence`` against the drift Kepler's third law predicts.

Two element sets that disagree only about mean motion describe the same orbit
travelled at slightly different rates, so they separate *along track* by a
predictable amount: the rate difference in revolutions, times the circumference
of the orbit. That is arithmetic this file does itself, from the gravitational
parameter and the published mean motion, and it is what the propagated answer is
checked against — so agreement means the propagation is right rather than merely
self-consistent.

The second element set is made by editing the mean-motion field of the first and
recomputing its checksum. :func:`with_checksum` is proved against both published
lines before anything relies on it, because a line the propagator silently
rejected would otherwise look like a divergence of zero.

Marked as a unit test by living in ``tests/unit``: no database and no network.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet

# The ISS element set transcribed in ``test_look_angles_reference.py``, where
# its source, retrieval date and licence are recorded.
ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
EPOCH = datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC)

MEAN_MOTION_FIELD = "15.49815350"
"""Revolutions per day, columns 53–63 of line 2 of the element set format."""

MEAN_MOTION_REV_PER_DAY = float(MEAN_MOTION_FIELD)
PERTURBED_MEAN_MOTION_FIELD = "15.49825350"
"""The same to within 1e-4 revolutions per day — 0.036 s of orbital period,
from 5574.858 s to 5574.822 s. Small enough to be a plausible disagreement
between two fits of one orbit, large enough that the drift it causes is
kilometres within a day."""

MEAN_MOTION_DELTA_REV_PER_DAY = (
    float(PERTURBED_MEAN_MOTION_FIELD) - MEAN_MOTION_REV_PER_DAY
)

EARTH_MU_KM3_S2 = 398_600.4418
"""Earth's gravitational parameter, WGS-84. Vallado, *Fundamentals of
Astrodynamics and Applications*, appendix D."""

AGREEMENT_FRACTION = 0.01
"""One per cent. The closed form below models along-track drift alone, while the
propagator also carries the perigee and nodal precession an oblate Earth
produces, so exact agreement is not expected. Measured at 0.2 per cent."""


def with_checksum(line: str) -> str:
    """Recompute a line's modulo-10 checksum, in column 69.

    Digits contribute their value, a minus sign contributes one, and everything
    else contributes nothing — the rule the two-line element set format defines.
    """
    body = line[:68]
    total = sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in body)
    return body + str(total % 10)


def along_track_drift_km(elapsed_days: float) -> float:
    """How far apart the two orbits drift in ``elapsed_days``, from Kepler.

    The two differ only in mean motion, so after a while one is ahead of the
    other by ``delta_n * t`` revolutions of the same path. Multiplying by the
    circumference turns revolutions into kilometres.

    The semi-major axis comes from the period by Kepler's third law,
    ``a = (mu T^2 / 4 pi^2)^(1/3)``, and the orbit is near-circular
    (eccentricity 0.0003572) so its circumference is ``2 pi a``.
    """
    period_s = 86_400.0 / MEAN_MOTION_REV_PER_DAY
    semi_major_axis_km = (EARTH_MU_KM3_S2 * period_s**2 / (4 * math.pi**2)) ** (1 / 3)
    circumference_km = 2 * math.pi * semi_major_axis_km
    return abs(MEAN_MOTION_DELTA_REV_PER_DAY) * elapsed_days * circumference_km


@pytest.fixture(scope="module")
def service() -> SkyfieldOrbitService:
    """One service for the file — building a timescale parses a leap-second table."""
    return SkyfieldOrbitService()


@pytest.fixture
def iss() -> ElementSet:
    return ElementSet("norad:25544", EPOCH, ISS_LINE1, ISS_LINE2, "manual")


@pytest.fixture
def iss_refitted() -> ElementSet:
    """The same object, fitted to a very slightly different mean motion."""
    return ElementSet(
        "norad:25544",
        EPOCH,
        ISS_LINE1,
        with_checksum(
            ISS_LINE2.replace(MEAN_MOTION_FIELD, PERTURBED_MEAN_MOTION_FIELD)
        ),
        "manual",
    )


def test_the_checksum_rule_reproduces_both_published_lines() -> None:
    """Proves the helper before the perturbed element set depends on it.

    If the rule were wrong, the edited line would carry a bad checksum, and a
    propagator that rejected or mis-parsed it could report a divergence of zero
    — which would look exactly like the two orbits agreeing.
    """
    assert with_checksum(ISS_LINE1) == ISS_LINE1
    assert with_checksum(ISS_LINE2) == ISS_LINE2


def test_an_element_set_against_itself_is_exactly_zero(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Not approximately: the same input propagates to the same position."""
    assert service.element_set_divergence(iss, iss, EPOCH + timedelta(days=1)) == 0.0


def test_two_fits_drift_apart_by_the_amount_kepler_predicts(
    service: SkyfieldOrbitService, iss: ElementSet, iss_refitted: ElementSet
) -> None:
    """The measurement against closed-form arithmetic, at two separations.

    Both instants are checked because a single one can be matched by a constant
    offset as easily as by a drift; two fix the slope as well as the value.
    """
    for elapsed_days in (1.0, 3.0):
        measured_km = service.element_set_divergence(
            iss, iss_refitted, EPOCH + timedelta(days=elapsed_days)
        )
        expected_km = along_track_drift_km(elapsed_days)
        assert measured_km == pytest.approx(expected_km, rel=AGREEMENT_FRACTION)


def test_divergence_grows_with_time_from_the_epoch(
    service: SkyfieldOrbitService, iss: ElementSet, iss_refitted: ElementSet
) -> None:
    """Why the archive keeps every element set rather than the newest.

    Two fits agree near the epoch they share and separate afterwards, so the
    divergence is a measurement of how much trusting the older one costs — and
    that only exists if both are still on record (D-057).
    """
    measured_km = [
        service.element_set_divergence(iss, iss_refitted, EPOCH + timedelta(hours=h))
        for h in (1, 6, 24, 72)
    ]

    assert measured_km == sorted(measured_km)
    assert measured_km[0] < measured_km[-1]


def test_it_does_not_matter_which_set_is_named_first(
    service: SkyfieldOrbitService, iss: ElementSet, iss_refitted: ElementSet
) -> None:
    """A distance, not a signed difference — so the argument order is free."""
    at = EPOCH + timedelta(days=1)

    assert service.element_set_divergence(
        iss, iss_refitted, at
    ) == service.element_set_divergence(iss_refitted, iss, at)


def test_element_sets_for_different_objects_are_refused(
    service: SkyfieldOrbitService, iss: ElementSet, iss_refitted: ElementSet
) -> None:
    """Two satellites are thousands of km apart for reasons this cannot measure.

    Returning that number would put a figure into the divergence series that has
    nothing to do with element-set quality, and nothing downstream could tell it
    from a genuinely bad fit.
    """
    other_object = ElementSet(
        "norad:33591", EPOCH, iss_refitted.line1, iss_refitted.line2, "manual"
    )

    with pytest.raises(ValueError, match="different objects"):
        service.element_set_divergence(iss, other_object, EPOCH + timedelta(days=1))


def test_a_naive_datetime_is_refused_at_this_boundary(
    service: SkyfieldOrbitService, iss: ElementSet, iss_refitted: ElementSet
) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    with pytest.raises(ValueError, match="naive"):
        service.element_set_divergence(iss, iss_refitted, EPOCH.replace(tzinfo=None))

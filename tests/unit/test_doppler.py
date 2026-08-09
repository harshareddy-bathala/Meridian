"""``doppler_offset_hz`` against hand computation, and its sign against a pass.

Every expected value here is arithmetic written out in the test that uses it —
`f x v / c` with the numbers substituted — rather than a figure produced by the
function. A Doppler sign error keeps every magnitude plausible and inverts only
the direction a station retunes, so it is the failure most likely to survive a
test that checks the shape of the answer instead of its value.

Marked as a unit test by living in ``tests/unit``: no database and no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.doppler import SPEED_OF_LIGHT_KM_S, doppler_offset_hz
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.registry.doppler_tolerance import (
    SPEED_OF_LIGHT_KM_S as REGISTRY_SPEED_OF_LIGHT_KM_S,
)

METEOR_CENTRE_FREQ_HZ = 137_100_000
"""Meteor-M LRPT, the project's primary reception target (CLAUDE.md)."""

APPROACH_RANGE_RATE_KM_S = -7.0
"""A satellite closing at 7 km/s — near the fastest a low Earth pass produces,
and negative because :class:`~meridian.orbit.types.LookAngle` is positive when
receding."""

EXPECTED_OFFSET_HZ = 3201.215
"""``137_100_000 x 7.0 / 299_792.458``, worked out by hand to four figures.

Independent of the implementation: it is the classical Doppler formula with
these two numbers substituted, not the function's own answer recorded."""


def test_an_approaching_satellite_raises_the_observed_frequency() -> None:
    """Sign and magnitude together, against the hand-computed value.

    Positive means the signal arrives *above* nominal, which is what a station
    closing on a transmitter observes. Getting this backwards would send every
    station the wrong way through every pass.
    """
    offset_hz = doppler_offset_hz(APPROACH_RANGE_RATE_KM_S, METEOR_CENTRE_FREQ_HZ)

    assert offset_hz == pytest.approx(EXPECTED_OFFSET_HZ, abs=0.01)
    assert offset_hz > 0


def test_a_receding_satellite_lowers_it_by_the_same_amount() -> None:
    """The curve is antisymmetric: the same speed away is the same shift down."""
    approaching = doppler_offset_hz(APPROACH_RANGE_RATE_KM_S, METEOR_CENTRE_FREQ_HZ)
    receding = doppler_offset_hz(-APPROACH_RANGE_RATE_KM_S, METEOR_CENTRE_FREQ_HZ)

    assert receding == pytest.approx(-approaching)
    assert receding < 0


def test_a_satellite_at_closest_approach_is_on_frequency() -> None:
    """Range rate is zero at the moment the distance stops shrinking."""
    assert doppler_offset_hz(0.0, METEOR_CENTRE_FREQ_HZ) == 0.0


def test_the_shift_at_137_mhz_is_the_few_kilohertz_the_project_quotes() -> None:
    """Sanity against the figure the docstrings and CLAUDE.md both state.

    Roughly ±3 kHz across a pass. A result in the hertz or the megahertz would
    mean a unit slipped — kilometres for metres, or MHz for Hz — and the formula
    would still look right.
    """
    fastest = abs(doppler_offset_hz(-8.0, METEOR_CENTRE_FREQ_HZ))
    assert 2_000 < fastest < 5_000


def test_the_shift_is_proportional_to_the_carrier_frequency() -> None:
    """Why the tolerance in ``registry.doppler_tolerance`` is fractional.

    The 437 MHz band the station's Tier 3 build adds (D-053) shifts more than
    three times as far at the same range rate, so any fixed-hertz assumption
    made at 137 MHz would be wrong there.
    """
    at_137 = doppler_offset_hz(APPROACH_RANGE_RATE_KM_S, 137_100_000)
    at_437 = doppler_offset_hz(APPROACH_RANGE_RATE_KM_S, 437_000_000)

    assert at_437 / at_137 == pytest.approx(437_000_000 / 137_100_000)


def test_the_two_definitions_of_the_speed_of_light_agree() -> None:
    """D-061: the constant is written twice on purpose, and held equal here.

    ``registry.doppler_tolerance`` is a leaf that imports nothing from
    ``meridian``, and coupling it to the orbit module to share one float would
    cost more than the duplication does. What the duplication cannot be allowed
    to do is drift, and nothing but this assertion prevents that.
    """
    assert SPEED_OF_LIGHT_KM_S == REGISTRY_SPEED_OF_LIGHT_KM_S


# --- the service wiring -------------------------------------------------------

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
BLUFFTON = GroundSite(lat_deg=+40.8939, lon_deg=-83.8917, alt_m=0.0)
CULMINATION = datetime(2014, 1, 23, 6, 26, 57, tzinfo=UTC)
"""The culmination of the second pass of that morning, to the second.

Taken from Skyfield's own event finder in
``tests/unit/test_pass_windows_reference.py``, which is where the element set
and site below are transcribed and attributed."""


@pytest.fixture
def iss() -> ElementSet:
    return ElementSet(
        satellite_id="norad:25544",
        epoch=datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC),
        line1=ISS_LINE1,
        line2=ISS_LINE2,
        source="manual",
    )


def test_the_shift_falls_through_zero_at_culmination(iss: ElementSet) -> None:
    """The one shape check worth making on a real pass.

    Before culmination the satellite is closing and the shift is positive;
    after, it is receding and the shift is negative. The crossing is what a
    station tuning through a pass actually experiences, and it ties the pure
    function to the propagator's sign convention — which no test of either
    alone can do.
    """
    samples = SkyfieldOrbitService().doppler_curve(
        iss,
        BLUFFTON,
        METEOR_CENTRE_FREQ_HZ,
        [CULMINATION + timedelta(minutes=offset) for offset in (-2, 0, 2)],
    )

    before, at_peak, after = samples
    assert before.offset_hz > 0
    assert after.offset_hz < 0
    assert abs(at_peak.offset_hz) < abs(before.offset_hz)


def test_one_sample_is_returned_per_instant_asked_for(iss: ElementSet) -> None:
    """Order and length preserved, so a caller can zip them with its own times."""
    times = [CULMINATION + timedelta(seconds=s) for s in (0, 30, 60)]
    samples = SkyfieldOrbitService().doppler_curve(
        iss, BLUFFTON, METEOR_CENTRE_FREQ_HZ, times
    )

    assert [sample.t for sample in samples] == times


def test_a_naive_datetime_is_refused_at_this_boundary(iss: ElementSet) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    with pytest.raises(ValueError, match="naive"):
        SkyfieldOrbitService().doppler_curve(
            iss, BLUFFTON, METEOR_CENTRE_FREQ_HZ, [CULMINATION.replace(tzinfo=None)]
        )

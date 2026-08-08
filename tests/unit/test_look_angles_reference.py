"""``SkyfieldOrbitService.look_angles`` against published values.

**The numbers below are not ours.** They are the printed output of the worked
example in Skyfield's own "Earth Satellites" documentation, transcribed with the
element set, the ground site and the instant that produced them. A propagator
tested only against its own output is tested against nothing; this file is the
one place in the orbit module where an external answer is the authority.

*What this proves and what it does not.* Our conversion pipeline — element set
in, topocentric look angles out, in the units and sign conventions this project
uses — is checked end to end. It is **not** an independent check of the frame
mathematics, because the reference and the implementation share a library: an
error inside Skyfield's TEME-to-Earth-fixed rotation would appear in both and
this file would stay green. Closing that gap needs a fixture from a tool that is
not Skyfield, and is worth doing when there is a reason to doubt the library
rather than as a matter of routine.

No database and no network: marked as a unit test by living in ``tests/unit``,
and the service loads its timescale from Skyfield's bundled data.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 6; ATTRIBUTION.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite, LookAngle

# --- transcribed from the reference, not computed here -------------------------
#
# Source: Skyfield documentation, "Earth Satellites"
#         https://rhodesmill.org/skyfield/earth-satellites.html
# Retrieved: 2026-08-08 · Licence: MIT
#
# The ISS element set, the Bluffton site and the instant used throughout that
# page's worked example.

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"

BLUFFTON = GroundSite(lat_deg=+40.8939, lon_deg=-83.8917, alt_m=0.0)
REFERENCE_INSTANT = datetime(2014, 1, 23, 11, 18, 7, tzinfo=UTC)

# Printed by the reference as: Altitude: 16deg 16' 32.6"
#                              Azimuth: 350deg 15' 20.4"
#                              Distance: 1168.7 km
REFERENCE_ELEVATION_DEG = 16 + 16 / 60 + 32.6 / 3600
REFERENCE_AZIMUTH_DEG = 350 + 15 / 60 + 20.4 / 3600
REFERENCE_RANGE_KM = 1168.7

ANGLE_TOLERANCE_DEG = 1e-4
"""Looser than our agreement with the reference, tighter than its own precision.

The published angles are rounded to a tenth of an arcsecond, which is 2.8e-5
degrees, so no assertion can be tighter than that and mean anything. We agree to
about 1e-5 degrees. A tolerance of 1e-4 sits between the two: it cannot fail on
the reference's rounding, and it would fail on any real change to the pipeline.
"""

RANGE_TOLERANCE_KM = 0.05
"""Half the reference's own printing precision.

Distance is published to 0.1 km, so 0.05 km is exactly the rounding bound and
not a number chosen to make the test pass.
"""


@pytest.fixture(scope="module")
def service() -> SkyfieldOrbitService:
    """One service for the file — building a timescale parses a leap-second table."""
    return SkyfieldOrbitService()


@pytest.fixture
def iss() -> ElementSet:
    return ElementSet(
        satellite_id="norad:25544",
        epoch=datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC),
        line1=ISS_LINE1,
        line2=ISS_LINE2,
        source="manual",
    )


def one_angle_at(
    service: SkyfieldOrbitService, iss: ElementSet, t: datetime
) -> LookAngle:
    """Look angles for the single instant ``t``.

    A one-second half-open window yields exactly one sample, which keeps these
    assertions about the geometry rather than about the sampler.
    """
    angles = service.look_angles(iss, BLUFFTON, t, t + timedelta(seconds=1), step_s=1.0)
    assert len(angles) == 1
    return angles[0]


def test_look_angles_match_the_published_values(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """The whole point of the file: an external answer, reproduced."""
    angle = one_angle_at(service, iss, REFERENCE_INSTANT)

    assert angle.elevation_deg == pytest.approx(
        REFERENCE_ELEVATION_DEG, abs=ANGLE_TOLERANCE_DEG
    )
    assert angle.azimuth_deg == pytest.approx(
        REFERENCE_AZIMUTH_DEG, abs=ANGLE_TOLERANCE_DEG
    )
    assert angle.range_km == pytest.approx(REFERENCE_RANGE_KM, abs=RANGE_TOLERANCE_KM)


def test_the_reference_pass_is_approaching_so_range_rate_is_negative(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Sign, asserted on its own because a sign error is otherwise silent.

    The reference publishes no range rate, so this is checked against the
    geometry instead: the satellite is climbing — 16 degrees and rising toward a
    culmination a few minutes later — which means it is closing on the station.
    `LookAngle.range_rate_km_s` is documented positive when receding, so this
    must be negative, and the range a minute later must be smaller.

    A sign error here inverts every Doppler shift the platform ever publishes
    while leaving all the magnitudes plausible.
    """
    angle = one_angle_at(service, iss, REFERENCE_INSTANT)
    later = one_angle_at(service, iss, REFERENCE_INSTANT + timedelta(minutes=1))

    assert angle.range_rate_km_s < 0
    assert later.range_km < angle.range_km


def test_the_result_is_sensitive_to_the_earth_fixed_frame(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """The mutation check ``orbit/__init__.py`` promises, in the form it can take.

    We do not perform the TEME-to-Earth-fixed rotation ourselves — Skyfield does
    it inside ``satellite - observer`` — so there is no rotation of ours to
    disable. What can be checked, and matters just as much, is that the answer
    is *coupled* to the observation time through that rotation: the Earth turns
    one degree every four minutes beneath the orbit, so an implementation that
    evaluated the observer's position at a fixed instant would drift from the
    truth by degrees within minutes.

    Four minutes later, the answer must have moved by far more than the
    tolerance the reference assertion uses. If it has not, the site is no longer
    being carried by the rotating Earth and the reference test above has stopped
    proving anything.
    """
    angle = one_angle_at(service, iss, REFERENCE_INSTANT)
    four_minutes_on = one_angle_at(
        service, iss, REFERENCE_INSTANT + timedelta(minutes=4)
    )

    moved_deg = abs(four_minutes_on.elevation_deg - angle.elevation_deg)
    assert moved_deg > ANGLE_TOLERANCE_DEG * 1000


# --- the sampler --------------------------------------------------------------


def test_the_sample_window_is_half_open(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Ten seconds at two-second steps is five samples, not six.

    The instant at ``end`` belongs to the next window. Two adjacent look-angle
    runs stitched together must not both command the antenna for the same moment.
    """
    angles = service.look_angles(
        iss,
        BLUFFTON,
        REFERENCE_INSTANT,
        REFERENCE_INSTANT + timedelta(seconds=10),
        step_s=2.0,
    )

    assert [angle.t for angle in angles] == [
        REFERENCE_INSTANT + timedelta(seconds=offset) for offset in (0, 2, 4, 6, 8)
    ]


def test_timestamps_do_not_drift_across_a_long_pass(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Each instant is start plus a whole multiple of the step, not the last plus one.

    A third of a second is not representable in binary and `timedelta` stores
    whole microseconds, so the two constructions differ measurably. Multiplying
    rounds once, at the end: under a microsecond of error however long the pass.
    Adding repeatedly rounds the step first and then repeats that rounded value,
    so the error grows with the sample count — for a fifteen-minute pass at this
    step it reaches most of a millisecond, which is a thousand times worse and
    lands in the timestamps a rotator is driven by.

    Both bounds are asserted, because "under a microsecond" only means something
    next to what the alternative would have cost.
    """
    angles = service.look_angles(
        iss,
        BLUFFTON,
        REFERENCE_INSTANT,
        REFERENCE_INSTANT + timedelta(minutes=15),
        step_s=1 / 3,
    )

    exact_s = (len(angles) - 1) / 3
    actual_s = (angles[-1].t - REFERENCE_INSTANT).total_seconds()
    assert actual_s == pytest.approx(exact_s, abs=1e-6)

    accumulated = REFERENCE_INSTANT
    for _ in range(len(angles) - 1):
        accumulated += timedelta(seconds=1 / 3)
    accumulated_error_s = abs(
        (accumulated - REFERENCE_INSTANT).total_seconds() - exact_s
    )
    assert accumulated_error_s > 1e-4


def test_an_empty_or_reversed_interval_yields_nothing(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Zero-length and back-to-front both return [], rather than one stray sample."""
    assert (
        service.look_angles(
            iss, BLUFFTON, REFERENCE_INSTANT, REFERENCE_INSTANT, step_s=1.0
        )
        == []
    )
    assert (
        service.look_angles(
            iss,
            BLUFFTON,
            REFERENCE_INSTANT,
            REFERENCE_INSTANT - timedelta(minutes=1),
            step_s=1.0,
        )
        == []
    )


def test_a_naive_datetime_is_refused_at_this_boundary(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance.

    Refused here rather than deeper, where the offset would already have been
    silently taken as UTC and the whole pass shifted by the local timezone.
    """
    naive = REFERENCE_INSTANT.replace(tzinfo=None)

    with pytest.raises(ValueError, match="naive"):
        service.look_angles(
            iss, BLUFFTON, naive, naive + timedelta(minutes=1), step_s=1.0
        )


def test_a_non_positive_step_is_refused(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Zero would loop forever if the sampler were written the obvious way."""
    with pytest.raises(ValueError, match="step_s"):
        service.look_angles(
            iss,
            BLUFFTON,
            REFERENCE_INSTANT,
            REFERENCE_INSTANT + timedelta(minutes=1),
            step_s=0.0,
        )


def test_azimuth_is_unwrapped_across_the_series(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Whatever the pass does, no consecutive pair jumps most of a turn.

    The unwrapper has its own exhaustive tests; this asserts that
    ``look_angles`` actually applies it, which no test of the pure function can.
    """
    angles = service.look_angles(
        iss,
        BLUFFTON,
        REFERENCE_INSTANT - timedelta(minutes=30),
        REFERENCE_INSTANT + timedelta(minutes=30),
        step_s=10.0,
    )

    steps = [
        abs(later.azimuth_deg - earlier.azimuth_deg)
        for earlier, later in pairwise(angles)
    ]
    assert max(steps) < 180.0

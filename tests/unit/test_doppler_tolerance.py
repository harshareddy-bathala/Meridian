"""The frequency tolerance behind ``Registry.was_listening()``.

Pure arithmetic, so every case is stated rather than measured. The expected
values are computed independently from the physics — ``Δf = f × v / c`` with the
figures named in the module's own docstring — rather than by calling the function
under test, which would pass whenever both were wrong together.

Reference: docs/DECISIONS.md D-056; docs/GLOSSARY.md on Doppler shift.
"""

from __future__ import annotations

import pytest

from meridian.registry.doppler_tolerance import doppler_tolerance_hz

SPEED_OF_LIGHT_KM_S = 299_792.458
MAX_RANGE_RATE_KM_S = 8.0

METEOR_LRPT_HZ = 137_900_000
NOAA_APT_HZ = 137_912_500
UHF_SATELLITE_HZ = 437_000_000


def expected_hz(centre_freq_hz: int) -> int:
    """The shift the classical formula gives, derived here independently."""
    return round(centre_freq_hz * MAX_RANGE_RATE_KM_S / SPEED_OF_LIGHT_KM_S)


@pytest.mark.parametrize(
    "centre_freq_hz", [137_100_000, METEOR_LRPT_HZ, UHF_SATELLITE_HZ, 1_700_000_000]
)
def test_the_tolerance_is_the_classical_doppler_shift(centre_freq_hz: int) -> None:
    assert doppler_tolerance_hz(centre_freq_hz) == expected_hz(centre_freq_hz)


def test_the_tolerance_scales_with_frequency() -> None:
    """Fractional, not fixed — this is the whole reason for the design.

    437 MHz is a little over three times 137 MHz, and the tolerance must grow
    with it. A fixed window right at VHF would be three times too tight at UHF,
    and a station correctly tracking a 437 MHz pass would read as not listening.
    """
    vhf = doppler_tolerance_hz(METEOR_LRPT_HZ)
    uhf = doppler_tolerance_hz(UHF_SATELLITE_HZ)

    assert uhf > vhf
    assert uhf / vhf == pytest.approx(UHF_SATELLITE_HZ / METEOR_LRPT_HZ, rel=1e-3)


def test_the_tolerance_stays_inside_the_bands_channel_spacing() -> None:
    """The risk a tolerance creates, checked rather than asserted in prose.

    A window wide enough to reach the next satellite in the band would credit a
    station listening to one for another. The closest pair this project cares
    about is NOAA at 137.9125 MHz against Meteor at 137.900 MHz — 12.5 kHz — and
    the tolerance has to stay comfortably under half that gap so the two windows
    cannot overlap.
    """
    gap_hz = NOAA_APT_HZ - METEOR_LRPT_HZ
    assert gap_hz == 12_500

    assert doppler_tolerance_hz(METEOR_LRPT_HZ) < gap_hz / 2


def test_the_vhf_tolerance_is_a_few_kilohertz() -> None:
    """A sanity bound in the units a reader thinks in.

    Roughly ±3.7 kHz at 137 MHz, which matches the ±3 kHz figure quoted for
    LEO Doppler throughout this project's documentation — the small excess is
    MAX_RANGE_RATE_KM_S being a deliberate over-estimate.
    """
    assert 3_000 < doppler_tolerance_hz(METEOR_LRPT_HZ) < 4_000


def test_a_zero_frequency_has_no_tolerance() -> None:
    """Degenerate, and it must not become a negative bound.

    `was_listening` subtracts the tolerance to build a lower bound, so a
    negative return here would widen the window instead of narrowing it.
    """
    assert doppler_tolerance_hz(0) == 0

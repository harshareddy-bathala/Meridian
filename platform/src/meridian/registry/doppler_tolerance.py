"""How far off its nominal frequency a station may be and still be listening.

``Registry.was_listening()`` compares the frequency a station reported in its
heartbeat against the frequency an assignment named. Those are not equal for any
station that works: the Doppler shift across a low-Earth pass is wider than the
receiver's passband, so a station retunes continuously and reports whatever it is
tuned to at that instant. This module states how much difference is still the
same target.

A leaf, like ``registry.liveness``: it imports nothing from ``meridian``,
performs no I/O and reads no clock. The registry calls it to turn one frequency
into a bound, and hands the store layer an explicit range — deciding what counts
as "the same frequency" is a domain judgement and does not belong in SQL.

Reference: docs/DECISIONS.md D-056, D-028; Vallado, *Fundamentals of
Astrodynamics and Applications*, ch. 4 (range rate); docs/GLOSSARY.md on Doppler.
"""

from __future__ import annotations

__all__ = [
    "MAX_RANGE_RATE_KM_S",
    "SPEED_OF_LIGHT_KM_S",
    "doppler_tolerance_hz",
]

SPEED_OF_LIGHT_KM_S = 299_792.458
"""Exact by definition — the metre is defined from it (SI, 1983)."""

MAX_RANGE_RATE_KM_S = 8.0
"""An upper bound on how fast a low-Earth satellite closes on a ground station.

Not a measurement: a bound chosen to be safely above the physical maximum. A
circular orbit at 400 km has an orbital speed near 7.67 km/s, and the *range*
rate — the component along the line of sight, which is what shifts the frequency
— is strictly less than that, reaching its maximum near the horizon where the
satellite is moving almost directly toward or away from the observer. Adding the
station's own motion with the Earth's rotation (up to about 0.46 km/s at the
equator) still leaves the total under 8.

Rounded up deliberately. Being generous here widens the tolerance and risks
crediting a station for the wrong satellite; being tight risks calling a
correctly-tuned station "not listening", which turns a real miss into an
unexplained absence and takes it out of the reliability figures altogether. The
first failure is bounded by the check in :func:`doppler_tolerance_hz`'s
docstring; the second is silent.
"""


def doppler_tolerance_hz(centre_freq_hz: int) -> int:
    """The largest frequency offset still attributable to Doppler shift.

    Classical (non-relativistic) approximation, ``Δf = f × v / c``. At orbital
    velocities the relativistic correction is far below one hertz and is
    irrelevant against a tolerance in the kilohertz.

    Args:
        centre_freq_hz: The nominal frequency an assignment named, in Hz.

    Returns:
        A non-negative offset in Hz. A station reported within this much of
        ``centre_freq_hz`` is tuned to the same target.

    Note:
        **Fractional, not fixed**, because Doppler is proportional to frequency.
        A fixed ±4 kHz would be right at 137 MHz and three times too tight at
        437 MHz, which is the band the station's Tier 3 build adds (D-053).

        The risk a tolerance creates is crediting a station listening to one
        satellite for another nearby in the band. At 137 MHz this returns under
        4 kHz, and the closest pair this project cares about — NOAA at
        137.9125 MHz against Meteor at 137.900 MHz — is 12.5 kHz apart. The
        property that has to hold is that the tolerance stays narrower than the
        band's channel spacing, and it does.
    """
    return round(centre_freq_hz * MAX_RANGE_RATE_KM_S / SPEED_OF_LIGHT_KM_S)

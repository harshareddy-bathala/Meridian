"""Doppler shift — how far a pass moves a signal off its nominal frequency.

A satellite approaching a station compresses the wavefront and raises the
observed frequency; receding lowers it. Across a low Earth orbit pass at
137 MHz the shift sweeps roughly ±3 kHz, which is wider than the receiver's
passband — so a station retunes continuously through a pass instead of sitting
on the nominal frequency, and this is the curve it retunes along.

Plain arithmetic on numbers the caller supplies: no propagator, no clock and no
I/O, so the sign convention can be checked against hand computation rather than
against a pass. ``meridian.orbit.skyfield_service`` obtains the range rates and
pairs each offset with the instant it belongs to.

Reference: Vallado, *Fundamentals of Astrodynamics and Applications*, ch. 4
(range rate); docs/MSP-SPEC.md §4.4 for the wire form; docs/GLOSSARY.md.
"""

from __future__ import annotations

__all__ = ["SPEED_OF_LIGHT_KM_S", "doppler_offset_hz"]

SPEED_OF_LIGHT_KM_S = 299_792.458
"""Exact by definition — the metre is defined from it (SI, 1983).

``meridian.registry.doppler_tolerance`` states the same value, because it needs
it to bound how far off frequency a station may report and still count as
listening. The two modules answer different questions — a bound there, a
measurement here — and neither imports the other, so the value is written twice
and ``tests/unit/test_doppler.py`` holds the two copies equal (D-061).
"""


def doppler_offset_hz(range_rate_km_s: float, centre_freq_hz: int) -> float:
    """Frequency offset from nominal, at one instant.

    Classical (non-relativistic) approximation, ``Δf = −f₀ · ṙ / c``. At orbital
    velocities the relativistic correction is below 0.01 Hz, far under any
    measurement this project makes.

    Args:
        range_rate_km_s: Rate of change of the station-to-satellite distance.
            **Negative while the satellite is approaching**, matching
            :class:`~meridian.orbit.types.LookAngle`.
        centre_freq_hz: The nominal transmit frequency.

    Returns:
        Offset in Hz, positive when the observed frequency is *higher* than
        nominal — which is what an approaching satellite produces. Add it to
        ``centre_freq_hz`` to get the frequency to tune to.

    Note:
        The minus sign is most of what this function contains, and the one thing
        that can be wrong without looking wrong: invert it and every magnitude
        stays plausible while every station retunes the wrong way through every
        pass. It is asserted directly rather than left to a round trip.
    """
    return -centre_freq_hz * range_rate_km_s / SPEED_OF_LIGHT_KM_S

"""Range and range rate along the line of sight, from topocentric vectors.

Split from :mod:`meridian.orbit.skyfield_service`, which places a satellite
and hands its vectors here. Plain arithmetic on plain lists: no propagator,
no frame conversion, so the sign of the result can be checked by hand.

Reference: Vallado, *Fundamentals of Astrodynamics and Applications*, ch. 4.
"""

from __future__ import annotations

import math

__all__ = ["range_and_rate"]


def range_and_rate(
    position_km: list[float], velocity_km_s: list[float]
) -> tuple[float, float]:
    """Distance along the line of sight, and how fast it is changing.

    Args:
        position_km: Station-to-satellite vector, in km.
        velocity_km_s: Its rate of change, in km/s. Both are topocentric and in
            the same frame; this function does no conversion and would return a
            confidently wrong number if handed vectors from two different ones.

    Returns:
        Range in km, and range rate in km/s. **Positive range rate means
        receding**, matching :class:`~meridian.orbit.types.LookAngle`'s
        documented sign.

    Note:
        Range rate is ``(r · v) / |r|`` — the component of the relative velocity
        along the unit vector pointing at the satellite. The perpendicular
        components change where the antenna points but not how far away the
        satellite is, so they do not shift the frequency.

        Getting the sign wrong here is silent: every magnitude stays plausible
        and only the direction of the Doppler shift inverts, which looks exactly
        like a correct answer until a station retunes the wrong way.

        Reference: Vallado, ch. 4.
    """
    range_km = math.sqrt(sum(component**2 for component in position_km))
    radial_km2_s = sum(r * v for r, v in zip(position_km, velocity_km_s, strict=True))
    return range_km, radial_km2_s / range_km

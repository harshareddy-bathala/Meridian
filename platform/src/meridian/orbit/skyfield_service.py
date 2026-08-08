"""Orbit propagation and look angles, using Skyfield.

Answers "where is this satellite from this ground station, right now" — the
pointing, the distance and how fast the distance is changing — for a satellite
described by a two-line element set. That is what a rotator needs in order to
track a pass and what a receiver needs in order to follow the Doppler shift.

Propagation is confined to this module, so the rest of the platform depends on
the ``OrbitService`` interface rather than on any particular propagator. If you
are changing how orbits are computed, this is the only file involved.

Takes no database connection, opens no sockets and reads no clock: element sets
and instants are both supplied by the caller. A prediction is therefore
reproducible from a stored element set and a stored time, which is what makes
the numbers in an evaluation report checkable months later.

The timescale is built from Skyfield's bundled leap-second data, so nothing here
needs the network — see ``tests/unit/test_propagator_independence.py``, which
holds that property in place. No planetary ephemeris is loaded; Earth-satellite
work does not need one.

**On frames.** SGP4 produces positions in TEME, which is not the frame a ground
station observes in. Getting from one to the other depends on the observation
time, because the Earth turns 15° an hour beneath the orbit. Skyfield performs
that conversion inside ``satellite - observer``;
``tests/unit/test_look_angles_reference.py`` records what our tests do and do
not prove about it.

Reference: Vallado, *Fundamentals of Astrodynamics and Applications*, ch. 3–4
(frames, range rate). See docs/GLOSSARY.md for TEME, AOS and LOS.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta

from skyfield.api import EarthSatellite, load, wgs84

from meridian.orbit.azimuth_continuity import unwrap_azimuth_deg
from meridian.orbit.types import ElementSet, GroundSite, LookAngle, require_utc

__all__ = ["SkyfieldOrbitService"]


class SkyfieldOrbitService:
    """Propagation, look angles, Doppler and uncertainty, using Skyfield.

    Deliberately not declared to inherit from
    :class:`~meridian.orbit.service.OrbitService` — Python resolves the protocol
    structurally, so mypy already checks conformance at every call site that
    expects one.

    Constructed once and reused. The timescale it holds is immutable and
    building it parses a leap-second table, so a service per request would
    repeat that work on every prediction for no benefit.

    A **partial** implementation: ``look_angles`` is here. ``pass_windows``,
    ``doppler_curve``, ``timing_uncertainty`` and ``element_set_divergence``
    arrive in the sessions that follow, each with the tests that check it.
    """

    def __init__(self) -> None:
        """Build the timescale from Skyfield's bundled leap-second data."""
        # builtin=True is Skyfield's current default, written out because the
        # station has to work with the network down and a library default could
        # change in a future release without us noticing.
        self._timescale = load.timescale(builtin=True)

    def look_angles(
        self,
        element_set: ElementSet,
        site: GroundSite,
        start: datetime,
        end: datetime,
        *,
        step_s: float,
    ) -> list[LookAngle]:
        """See :meth:`meridian.orbit.service.OrbitService.look_angles`.

        Azimuth is unwrapped across the whole series before it is returned, so a
        pass crossing north reads as 359°, 361° rather than sending a rotator
        the long way round.
        """
        if step_s <= 0:
            raise ValueError(f"step_s must be positive, got {step_s}")

        sample_times = _sample_times(
            require_utc(start, "start"), require_utc(end, "end"), step_s
        )
        if not sample_times:
            return []

        wrapped = [self._sample(element_set, site, t) for t in sample_times]
        unwrapped_deg = unwrap_azimuth_deg([angle.azimuth_deg for angle in wrapped])
        return [
            replace(angle, azimuth_deg=azimuth_deg)
            for angle, azimuth_deg in zip(wrapped, unwrapped_deg, strict=True)
        ]

    def _sample(
        self, element_set: ElementSet, site: GroundSite, t: datetime
    ) -> LookAngle:
        """One instant's pointing and range, with azimuth still wrapped at north.

        Wrapped because unwrapping needs the sample before this one, and a single
        sample does not have it.
        """
        satellite = EarthSatellite(
            element_set.line1,
            element_set.line2,
            element_set.satellite_id,
            self._timescale,
        )
        observer = wgs84.latlon(site.lat_deg, site.lon_deg, elevation_m=site.alt_m)
        topocentric = (satellite - observer).at(self._timescale.from_datetime(t))
        elevation, azimuth, _ = topocentric.altaz()

        range_km, range_rate_km_s = _range_and_rate(
            [float(component) for component in topocentric.xyz.km],
            [float(component) for component in topocentric.velocity.km_per_s],
        )
        return LookAngle(
            t=t,
            azimuth_deg=float(azimuth.degrees),
            elevation_deg=float(elevation.degrees),
            range_km=range_km,
            range_rate_km_s=range_rate_km_s,
        )


def _sample_times(start: datetime, end: datetime, step_s: float) -> list[datetime]:
    """Instants at ``step_s`` intervals across ``[start, end)``.

    Half-open, matching the contract's wording and the window convention
    ``registry.was_listening`` uses, so two adjacent intervals cannot both sample
    one instant and count it twice.

    Each instant is ``start`` plus a whole multiple of the step rather than the
    previous instant plus a step: accumulating a float across a fifteen-minute
    pass drifts, and the drift ends up in the timestamps a rotator is driven by.
    The upper bound is then applied by comparison rather than by trusting
    ``span / step`` to round the way arithmetic says it should.
    """
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return []

    candidate_count = math.ceil(span_s / step_s)
    candidates = (start + timedelta(seconds=step_s * i) for i in range(candidate_count))
    return [t for t in candidates if t < end]


def _range_and_rate(
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

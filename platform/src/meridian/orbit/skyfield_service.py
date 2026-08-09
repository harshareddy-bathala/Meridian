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
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from skyfield.api import EarthSatellite, load, wgs84

from meridian.orbit.azimuth_continuity import unwrap_azimuth_deg
from meridian.orbit.doppler import doppler_offset_hz
from meridian.orbit.pass_search import (
    ElevationPass,
    ElevationScan,
    find_elevation_passes,
)
from meridian.orbit.time_sampling import half_open_sample_times
from meridian.orbit.types import (
    DopplerSample,
    ElementSet,
    GroundSite,
    LookAngle,
    PassSearch,
    PassWindow,
    TimingUncertainty,
    require_utc,
)
from meridian.orbit.uncertainty import timing_uncertainty_at_age

__all__ = ["PASS_SEARCH_MARGIN_S", "SkyfieldOrbitService"]

LookAngleAt = Callable[[datetime], LookAngle]
"""One satellite and one site bound together, leaving only time to supply."""

PASS_SEARCH_MARGIN_S = 1800.0
"""How far outside the requested interval :meth:`pass_windows` actually looks.

A pass straddling the edge of the interval has to be found whole, or it is
reported clipped here and clipped the other way by the next search along — the
same pass, counted twice and short both times. So the search widens by this at
both ends, and then keeps only the passes that *rise* inside the interval that
was asked for.

The margin has to exceed the longest possible pass. A satellite is above the
horizon while it lies within ``arccos(R_earth / (R_earth + h))`` of the station,
and it covers that arc at the orbital rate. At 850 km — the altitude of the
137 MHz weather satellites this project receives — that is 56.2° of a
101.8-minute orbit, so 15.9 minutes; at 1400 km it is 22.0 minutes. Thirty
minutes covers low Earth orbit with room, and a pass that outlasts it raises
rather than being returned clipped.

Reference: Vallado, *Fundamentals of Astrodynamics and Applications*, ch. 11.
"""


class SkyfieldOrbitService:
    """Propagation, look angles, Doppler and uncertainty, using Skyfield.

    Deliberately not declared to inherit from
    :class:`~meridian.orbit.service.OrbitService` — Python resolves the protocol
    structurally, so mypy already checks conformance at every call site that
    expects one.

    Constructed once and reused. The timescale it holds is immutable and
    building it parses a leap-second table, so a service per request would
    repeat that work on every prediction for no benefit.

    Complete: all five ``OrbitService`` methods are implemented here, so a
    caller annotated to take an ``OrbitService`` type-checks against this class
    without it having to say so.
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

        sample_times = half_open_sample_times(
            require_utc(start, "start"), require_utc(end, "end"), step_s
        )
        if not sample_times:
            return []

        look_angle_at = self._look_angle_at(element_set, site)
        wrapped = [look_angle_at(t) for t in sample_times]
        unwrapped_deg = unwrap_azimuth_deg([angle.azimuth_deg for angle in wrapped])
        return [
            replace(angle, azimuth_deg=azimuth_deg)
            for angle, azimuth_deg in zip(wrapped, unwrapped_deg, strict=True)
        ]

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        """See :meth:`meridian.orbit.service.OrbitService.pass_windows`.

        Searches :data:`PASS_SEARCH_MARGIN_S` beyond both ends of the requested
        interval and then keeps only the passes that *rise* inside it. Every
        window returned is therefore a whole pass, and two searches meeting at a
        boundary partition the passes between them rather than each reporting a
        clipped copy of the one that straddles it.

        A satellite that never sets over the site returns nothing: it has no
        acquisition inside the interval, and "when does it rise" is not a
        question a permanently visible object answers.

        Note:
            Two searches meeting at a seam refine the same acquisition on
            different coarse grids, so their answers can differ by up to the
            0.05 s refinement tolerance. A seam landing that close to a true
            acquisition — of order one boundary in a million — can therefore be
            claimed by both searches or by neither. A job splitting a horizon
            into consecutive searches should de-duplicate on satellite and
            acquisition rather than assume the split is exact (D-059).
        """
        look_angle_at = self._look_angle_at(search.element_set, search.site)

        def elevation_deg_at(t: datetime) -> float:
            return look_angle_at(t).elevation_deg

        margin = timedelta(seconds=PASS_SEARCH_MARGIN_S)
        found = find_elevation_passes(
            elevation_deg_at,
            ElevationScan(
                start=require_utc(search.start, "start") - margin,
                end=require_utc(search.end, "end") + margin,
                min_elevation_deg=search.min_elevation_deg,
                coarse_step_s=search.coarse_step_s,
            ),
        )
        return [
            self._to_pass_window(look_angle_at, search.element_set, elevation_pass)
            for elevation_pass in found
            if search.start <= elevation_pass.aos < search.end
        ]

    def doppler_curve(
        self,
        element_set: ElementSet,
        site: GroundSite,
        centre_freq_hz: int,
        times: Sequence[datetime],
    ) -> list[DopplerSample]:
        """See :meth:`meridian.orbit.service.OrbitService.doppler_curve`.

        The instants are taken as given rather than sampled here, so a caller
        can ask for exactly the times it already holds — the ones on a stored
        look-angle series, or the ones an observation reported.
        """
        look_angle_at = self._look_angle_at(element_set, site)
        return [
            DopplerSample(
                t=t,
                offset_hz=doppler_offset_hz(
                    look_angle_at(require_utc(t, "times")).range_rate_km_s,
                    centre_freq_hz,
                ),
            )
            for t in times
        ]

    def timing_uncertainty(
        self, element_set: ElementSet, at: datetime
    ) -> TimingUncertainty:
        """See :meth:`meridian.orbit.service.OrbitService.timing_uncertainty`.

        The propagator is not consulted: the Phase 1 answer depends only on how
        old the element set is, so the model lives in
        :mod:`meridian.orbit.uncertainty` and this method supplies the age.
        """
        return timing_uncertainty_at_age(element_set.age_s(require_utc(at, "at")))

    def element_set_divergence(
        self, a: ElementSet, b: ElementSet, at: datetime
    ) -> float:
        """See :meth:`meridian.orbit.service.OrbitService.element_set_divergence`.

        Both sets are propagated to ``at`` and the straight-line distance between
        the two positions is returned. The frame cancels: both go through the
        same conversion, so the separation is the same number whichever frame it
        is measured in, and only the choice of instant matters.

        Raises:
            ValueError: the two sets describe different objects, whose positions
                differ by thousands of kilometres for reasons that have nothing
                to do with element-set quality. The comparison is only
                meaningful within one satellite's own series.
        """
        if a.satellite_id != b.satellite_id:
            raise ValueError(
                f"element sets describe different objects: {a.satellite_id} "
                f"and {b.satellite_id}"
            )
        instant = require_utc(at, "at")
        return math.dist(
            self._geocentric_position_km(a, instant),
            self._geocentric_position_km(b, instant),
        )

    def _geocentric_position_km(
        self, element_set: ElementSet, t: datetime
    ) -> list[float]:
        """Where this element set puts its object at ``t``.

        Measured from Earth's centre rather than from any station, so two sets
        can be compared without a site entering the answer.
        """
        satellite = EarthSatellite(
            element_set.line1,
            element_set.line2,
            element_set.satellite_id,
            self._timescale,
        )
        position = satellite.at(self._timescale.from_datetime(t))
        return [float(component) for component in position.xyz.km]

    def _to_pass_window(
        self,
        look_angle_at: LookAngleAt,
        element_set: ElementSet,
        found: ElevationPass,
    ) -> PassWindow:
        """Name the satellite and element set a geometric pass came from.

        Raises:
            ValueError: the pass outlasts the searched margin, so its loss of
                signal was clipped rather than found — meaning
                :data:`PASS_SEARCH_MARGIN_S` is too small for this orbit. Raised
                rather than returned, because a scheduler cannot tell a window
                that ends early from one that ends when it says it does.
        """
        if found.is_truncated:
            raise ValueError(
                f"the pass acquired at {found.aos.isoformat()} outlasts the "
                f"{PASS_SEARCH_MARGIN_S} s search margin"
            )
        return PassWindow(
            satellite_id=element_set.satellite_id,
            aos=found.aos,
            los=found.los,
            max_elevation_deg=found.max_elevation_deg,
            max_elevation_at=found.max_elevation_at,
            aos_azimuth_deg=look_angle_at(found.aos).azimuth_deg,
            los_azimuth_deg=look_angle_at(found.los).azimuth_deg,
            element_set_epoch=element_set.epoch,
            element_set_age_s=element_set.age_s(found.aos),
            min_elevation_deg=found.min_elevation_deg,
        )

    def _look_angle_at(self, element_set: ElementSet, site: GroundSite) -> LookAngleAt:
        """Bind one satellite and one site, leaving a function of time.

        Parsing the element set and placing the observer happen once here rather
        than once per sample. A pass search evaluates the result several hundred
        times, and repeating that setup on each call dominates everything else
        the search does.

        Azimuth comes back still wrapped at north, because unwrapping needs the
        sample before this one and a single instant does not have it.
        """
        satellite = EarthSatellite(
            element_set.line1,
            element_set.line2,
            element_set.satellite_id,
            self._timescale,
        )
        observer = wgs84.latlon(site.lat_deg, site.lon_deg, elevation_m=site.alt_m)
        relative_position = satellite - observer

        def look_angle_at(t: datetime) -> LookAngle:
            topocentric = relative_position.at(self._timescale.from_datetime(t))
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

        return look_angle_at


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


if TYPE_CHECKING:
    from meridian.orbit.service import OrbitService

    def _conforms_to_the_protocol(service: SkyfieldOrbitService) -> OrbitService:
        """Never runs, never called — mypy checks it and that is the point.

        The class does not inherit from ``OrbitService``, because Python
        resolves protocols structurally and the annotation would add nothing.
        The cost of that is real, though: a method renamed here, or a signature
        changed on one side and not the other, would silently stop satisfying
        the interface and nothing would say so until a call site broke. This
        line is what says so, at type-check time.
        """
        return service

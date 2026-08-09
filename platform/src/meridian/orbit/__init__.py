"""Where satellites are, and when they are visible from a ground station.

Everything to do with orbits lives here: propagating a two-line element set,
working out when a satellite rises above a station's horizon, where to point at
it, and how far off its nominal frequency it will appear.

The pieces:

* ``service`` — the ``OrbitService`` interface the rest of the platform calls.
* ``types`` — the value types that cross the boundary, all angles in degrees,
  all frequencies integer hertz, all times timezone-aware UTC.
* ``skyfield_service`` — the implementation, built on the ``sgp4`` and
  ``skyfield`` libraries. It is the only module here that propagates anything.
* ``pass_search`` — finding the intervals a curve spends above a floor, given
  only a function from time to elevation.
* ``bracket_refinement`` — bisection and ternary search, narrowing a bracketed
  interval to a crossing or a peak.
* ``azimuth_continuity`` — unwrapping compass azimuth so a rotator never takes
  the long way round.
* ``doppler`` — turning a range rate into the frequency offset a station
  observes.
* ``uncertainty`` — how far out a predicted pass boundary is likely to be, from
  the age of the element set it was computed from.
* ``time_sampling`` — evenly spaced instants across an interval: the two
  endpoint rules the rest of the package needs, and the fixed grid that makes
  one pass get one answer whichever horizon found it.

Only ``skyfield_service`` propagates. The other six take a callable, a list of
numbers or a single measurement, which is what lets the geometry be checked
against curves and formulae whose answers are known in closed form, with no
element set and no propagator involved.

Orbit propagation is confined to this package, so the rest of the platform is
written against ``OrbitService`` and not against a particular propagator.

**Coordinate frames are where silent bugs live**, and this is the thing to slow
down for if you are new to the domain. SGP4 emits positions in TEME; that is not
the same as an Earth-fixed frame, which is not the same as what an observer on
the ground measures. Conversions between them depend on the observation time.
An error here does not crash — it produces plausible numbers that put the
antenna somewhere the satellite is not. ``docs/GLOSSARY.md`` defines the frames;
``tests/unit/test_look_angles_reference.py`` checks our conversions against
published values, and says plainly what that does and does not prove.
"""

from meridian.orbit.service import OrbitService
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

__all__ = [
    "DopplerSample",
    "ElementSet",
    "GroundSite",
    "LookAngle",
    "OrbitService",
    "PassSearch",
    "PassWindow",
    "TimingUncertainty",
    "require_utc",
]

"""Where satellites are, and when they are visible from a ground station.

Everything to do with orbits lives here: propagating a two-line element set,
working out when a satellite rises above a station's horizon, where to point at
it, and how far off its nominal frequency it will appear.

The pieces:

* ``service`` — the ``OrbitService`` interface the rest of the platform calls.
* ``types`` — the value types that cross the boundary, all angles in degrees,
  all frequencies integer hertz, all times timezone-aware UTC.
* ``skyfield_service`` — the implementation, built on the ``sgp4`` and
  ``skyfield`` libraries.
* ``azimuth_continuity`` — the one piece of look-angle work that needs no
  propagator, and so is tested on its own.

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

"""Propagation, element-set archive, uncertainty model.

**This module owns the ``sgp4``/``skyfield`` boundary. No other module imports
them** (docs/ARCHITECTURE.md rule 2). A ruff banned-import rule enforces it in CI.

Never reimplement SGP4. A hand-written propagator will be slower and subtly wrong,
and writing one teaches us nothing the library does not already encode.

Coordinate frames are where silent bugs live. TEME — what SGP4 outputs — is not
ECEF and is not topocentric. Convert deliberately and test against known
ground-truth passes; the test suite includes a mutation check that disables the
TEME→ECEF rotation and asserts the predictions move, so the suite is demonstrably
sensitive to the exact bug class this warning is about.
"""

from meridian.orbit.service import OrbitService
from meridian.orbit.types import (
    DopplerSample,
    ElementSet,
    GroundSite,
    LookAngle,
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
    "PassWindow",
    "TimingUncertainty",
    "require_utc",
]

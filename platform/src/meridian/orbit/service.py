"""The orbit module's interface to the rest of the platform.

This module owns the ``sgp4``/``skyfield`` boundary. **No other module imports
them** (docs/ARCHITECTURE.md rule 2), enforced by the ruff banned-import rule in
the workspace ``pyproject.toml`` rather than by review. Everything goes through
this protocol, so a propagator change never ripples outward.

Implementations are pure: no database, no network, no clock reads. The element-set
archive lives in ``meridian.orbit.archive``, which does touch the store; callers
see both through ``meridian.orbit``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from meridian.orbit.types import (
    DopplerSample,
    ElementSet,
    GroundSite,
    LookAngle,
    PassWindow,
    TimingUncertainty,
)

__all__ = ["OrbitService"]


class OrbitService(Protocol):
    """Propagation, look angles, Doppler and uncertainty for one satellite.

    Interfaces are defined before implementations, especially at module
    boundaries — so this exists ahead of the propagator behind it, and the
    registry, scheduler and pass-generation job code against it directly.
    """

    def pass_windows(
        self,
        element_set: ElementSet,
        site: GroundSite,
        start: datetime,
        end: datetime,
        *,
        min_elevation_deg: float = 0.0,
        coarse_step_s: float = 30.0,
    ) -> list[PassWindow]:
        """Every pass visible from ``site`` in ``[start, end)``.

        Implementations step coarsely to bracket each horizon crossing, then
        refine by bisection. ``coarse_step_s`` is exposed because it is a
        correctness parameter, not a performance knob: a step longer than the
        shortest pass steps over it entirely, and a low-elevation pass missed
        here is missing from the completeness denominator forever.
        """
        ...

    def look_angles(
        self,
        element_set: ElementSet,
        site: GroundSite,
        start: datetime,
        end: datetime,
        *,
        step_s: float,
    ) -> list[LookAngle]:
        """Pointing and range over ``[start, end)`` at ``step_s`` intervals.

        This is what drives a rotator, so azimuth is continuous modulo 360 rather
        than wrapped — a pass crossing north must not command a full rotation.
        """
        ...

    def doppler_curve(
        self,
        element_set: ElementSet,
        site: GroundSite,
        centre_freq_hz: int,
        times: Sequence[datetime],
    ) -> list[DopplerSample]:
        """Expected frequency offset at each instant in ``times``.

        ``f_observed = f_0 * (1 - range_rate / c)``, returned as the offset from
        ``centre_freq_hz`` to match the MSP §4.4 wire format.
        """
        ...

    def timing_uncertainty(self, element_set: ElementSet, at: datetime) -> TimingUncertainty:
        """1σ confidence in a pass boundary predicted from ``element_set``.

        Phase 1 returns a documented prior — never ``0.0``, which would claim
        perfect knowledge of an element set of unstated accuracy and would make
        MSP §4.3's ``timing_uncertainty_s`` actively misleading to a station
        deciding how wide to open its recording window.

        Phase 2 replaces the prior with a model fitted to measured timing error.
        The returned ``method`` string is what lets SC-3 compare the two.
        """
        ...

    def element_set_divergence(self, a: ElementSet, b: ElementSet, at: datetime) -> float:
        """Position difference in km between two element sets for the same object.

        Propagated to a common time ``at``. This is the raw measurement behind the
        ``element_set_divergence`` view and the uncertainty model that consumes it.
        """
        ...

"""How far from an assignment's frequency a recording may be and still be that pass.

The platform's ``registry.doppler_tolerance`` decides whether a heartbeat's tuned
frequency is the assignment's (D-056). A replayed recording has to pass the same
test before a station reports it against an assignment, and the client cannot
import the platform (ARCHITECTURE.md; tests/unit/test_import_boundaries.py), so
the rule is stated here too. The two are checked for agreement by a test that
imports both, as the observation rules are (tests/unit/test_observation_agreement.py).

Reference: docs/DECISIONS.md D-056.
"""

from __future__ import annotations

__all__ = ["doppler_tolerance_hz"]

SPEED_OF_LIGHT_KM_S = 299_792.458
MAX_RANGE_RATE_KM_S = 8.0
"""The platform's bound on low-Earth range rate, rounded up (D-056)."""


def doppler_tolerance_hz(centre_freq_hz: int) -> int:
    """The largest frequency offset still attributable to Doppler shift.

    Args:
        centre_freq_hz: The nominal frequency an assignment named, in Hz.

    Returns:
        A non-negative offset in Hz: under 4 kHz at 137 MHz, which keeps NOAA at
        137.9125 MHz distinct from Meteor at 137.900 MHz.
    """
    return round(centre_freq_hz * MAX_RANGE_RATE_KM_S / SPEED_OF_LIGHT_KM_S)

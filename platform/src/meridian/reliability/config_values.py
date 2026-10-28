"""What every reliability settings table is checked and hashed with.

Apart from :mod:`meridian.reliability.config` so that the tables kept in modules
of their own (:mod:`meridian.reliability.watch_config`) use the same checks and
the same hash without importing the module that assembles them.

Standard library only, as the configuration is.
"""

from __future__ import annotations

import hashlib
import json

__all__ = [
    "DAY_S",
    "ReliabilityConfigError",
    "canonical_sha256",
    "number",
    "share",
    "whole",
]

DAY_S = 86_400


class ReliabilityConfigError(ValueError):
    """The reliability configuration cannot be read, or holds a bad value."""


def whole(name: str, value: object, low: int, high: int) -> None:
    """Refuse ``value`` unless it is a whole number in ``low..high``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReliabilityConfigError(f"{name} must be a whole number, not {value!r}")
    if not low <= value <= high:
        raise ReliabilityConfigError(f"{name} must be in {low}..{high}, not {value}")


def share(name: str, value: object) -> None:
    """Refuse ``value`` unless it is a number strictly between 0 and 1."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReliabilityConfigError(f"{name} must be a number, not {value!r}")
    if not 0.0 < value < 1.0:
        raise ReliabilityConfigError(f"{name} must be between 0 and 1, not {value}")


def number(name: str, value: object, low: float, high: float) -> None:
    """Refuse ``value`` unless it is a number in ``low..high``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReliabilityConfigError(f"{name} must be a number, not {value!r}")
    if not low <= value <= high:
        raise ReliabilityConfigError(f"{name} must be in {low}..{high}, not {value}")


def canonical_sha256(parameters: dict[str, object]) -> bytes:
    """The sha256 of ``parameters`` as canonical JSON: keys sorted, no spaces."""
    text = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).digest()

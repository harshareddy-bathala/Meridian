"""How the receive-chain watch trains a baseline and judges a chain (Stage 28).

Two tables of the reliability file, ``[baseline]`` and ``[watch]``, kept apart
from :mod:`meridian.reliability.config`, which assembles them with the rest.
Strict as every table there is: an unknown key or a value out of range is
refused by name.

Reference: docs/DECISIONS.md D-293, D-294.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from meridian.reliability.config_values import (
    DAY_S,
    ReliabilityConfigError,
    canonical_sha256,
    number,
    whole,
)

__all__ = ["BaselineConfig", "WatchConfig"]

_DAY_S = DAY_S


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    """How a receive chain's baseline is trained (D-293).

    Changing one writes a new baseline for every chain beside the old.
    """

    bin_width_deg: float = 10.0
    """The width of an elevation bin."""

    min_elevation_deg: float = 5.0
    """Signal below this elevation is left out: it is mostly the horizon's."""

    train_s: int = 14 * _DAY_S
    """How far back from its end a baseline's training interval reaches."""

    retrain_s: int = 30 * _DAY_S
    """How old a baseline's interval may grow before a chain with no open
    warning is trained again. A chain with an open warning never is, so its
    shortfall is not learned as normal."""

    min_receptions: int = 5
    """Receptions a bin needs to be part of a baseline."""

    min_bins: int = 2
    """Bins a baseline needs: a shortfall is judged across elevations."""

    def __post_init__(self) -> None:
        """Refuse a value outside the range the tests were written for."""
        number("bin_width_deg", self.bin_width_deg, 1.0, 45.0)
        number("min_elevation_deg", self.min_elevation_deg, 0.0, 80.0)
        whole("train_s", self.train_s, _DAY_S, 365 * _DAY_S)
        whole("retrain_s", self.retrain_s, _DAY_S, 365 * _DAY_S)
        whole("min_receptions", self.min_receptions, 1, 1000)
        whole("min_bins", self.min_bins, 1, 18)

    def parameters(self) -> dict[str, float]:
        """The settings as recorded with every baseline, reals as reals."""
        held = asdict(self)
        return {
            one.name: float(held[one.name]) if one.type == "float" else held[one.name]
            for one in fields(self)
        }

    def sha256(self) -> bytes:
        """The hash of these settings alone; a baseline adds the diagnosis."""
        return canonical_sha256(dict(self.parameters()))


@dataclass(frozen=True, slots=True)
class WatchConfig:
    """When a chain's shortfall raises a warning, and when it clears (D-294).

    Changing one starts every chain's warnings afresh, beside the old rows.
    """

    recent_receptions: int = 3
    """How many of the chain's latest receptions a shortfall is judged over.
    The median of three is unmoved by one bad pass, and moved by two."""

    lookback_s: int = 7 * _DAY_S
    """How far back those receptions may lie: older signal is not recent."""

    min_bins: int = 2
    """Baseline bins a reception must cross to be judged, and bins that must
    fall short together to raise a warning."""

    raise_db: float = 3.0
    """The median shortfall, in decibels, that raises a warning."""

    clear_db: float = 1.5
    """The median shortfall at or below which an open warning clears. Below
    ``raise_db``, so a chain at the threshold does not raise and clear by turns."""

    def __post_init__(self) -> None:
        """Refuse a value outside the range the tests were written for."""
        whole("recent_receptions", self.recent_receptions, 1, 50)
        whole("lookback_s", self.lookback_s, 3_600, 90 * _DAY_S)
        whole("min_bins", self.min_bins, 1, 18)
        number("raise_db", self.raise_db, 0.1, 40.0)
        number("clear_db", self.clear_db, 0.0, 40.0)
        if self.clear_db >= self.raise_db:
            raise ReliabilityConfigError(
                f"clear_db ({self.clear_db}) must be below raise_db ({self.raise_db})"
            )

    def parameters(self) -> dict[str, float]:
        """The settings as recorded with every warning, reals as reals."""
        held = asdict(self)
        return {
            one.name: float(held[one.name]) if one.type == "float" else held[one.name]
            for one in fields(self)
        }

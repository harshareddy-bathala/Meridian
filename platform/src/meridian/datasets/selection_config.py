"""The ``[completeness]`` and ``[propensity]`` tables of the labelling configuration.

Both are selection-bias settings (Stage 16), hashed into every evaluation
dataset's manifest with the rest of the configuration, so a figure computed
under one threshold or one set of cells cannot be mistaken for another.

``[completeness]`` (D-150, D-151):

* ``threshold`` — the completeness at or above which a station-day is kept
  for primary evaluation, ``0.8``;
* ``sensitivity`` — the thresholds every report also states its counts at;
* ``archive_min_elevation_deg`` — the floor an archive station's computed pass
  must reach to count as available, applied to its peak;
* ``archive_match_tolerance_s`` — how far outside a computed window an archive
  reception may start and still be that pass, because the station's clock and
  elements are not ours.

``[propensity]`` (D-152):

* ``min_cell`` — the fewest available passes a cell needs before its own ratio
  is used rather than a coarser cell's;
* ``elevation_bands_deg`` — the edges between maximum-elevation bands;
* ``hour_band_h`` — the width of a local-solar-hour band, which must divide a
  day so every band is the same width.

Reference: docs/DECISIONS.md D-150, D-151, D-152.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.datasets.config_checks import (
    LabelConfigError,
    number,
    ratio,
    table,
    whole,
)

__all__ = [
    "CompletenessConfig",
    "PropensityConfig",
    "parse_completeness",
    "parse_propensity",
]

_ZENITH_DEG = 90.0
_HOURS = 24
_MAX_TOLERANCE_S = 3600
"""An hour either side is already a different pass of most LEO satellites."""

_MAX_MIN_CELL = 10_000

_COMPLETENESS = frozenset(
    (
        "threshold",
        "sensitivity",
        "archive_min_elevation_deg",
        "archive_match_tolerance_s",
    )
)
_PROPENSITY = frozenset(("min_cell", "elevation_bands_deg", "hour_band_h"))


@dataclass(frozen=True, slots=True)
class CompletenessConfig:
    """The ``[completeness]`` table (D-150, D-151)."""

    threshold: float = 0.8
    sensitivity: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9)
    archive_min_elevation_deg: float = 0.0
    archive_match_tolerance_s: int = 120

    def __post_init__(self) -> None:
        """Refuse a ratio outside 0..1, an unsorted list, or a floor off the sky."""
        ratio("completeness.threshold", self.threshold)
        if not self.sensitivity:
            message = "completeness.sensitivity must name at least one threshold"
            raise LabelConfigError(message)
        for one in self.sensitivity:
            ratio("completeness.sensitivity", one)
        _rising("completeness.sensitivity", self.sensitivity)
        floor = number(
            "completeness.archive_min_elevation_deg", self.archive_min_elevation_deg
        )
        if not 0 <= floor < _ZENITH_DEG:
            message = "completeness.archive_min_elevation_deg is outside 0..90"
            raise LabelConfigError(message)
        whole(
            "completeness.archive_match_tolerance_s",
            self.archive_match_tolerance_s,
            (0, _MAX_TOLERANCE_S),
        )

    def parameters(self) -> dict[str, object]:
        """The values, for the manifest."""
        return {
            "threshold": self.threshold,
            "sensitivity": list(self.sensitivity),
            "archive_min_elevation_deg": self.archive_min_elevation_deg,
            "archive_match_tolerance_s": self.archive_match_tolerance_s,
        }


@dataclass(frozen=True, slots=True)
class PropensityConfig:
    """The ``[propensity]`` table (D-152)."""

    min_cell: int = 20
    elevation_bands_deg: tuple[float, ...] = (15.0, 30.0, 60.0)
    hour_band_h: int = 4

    def __post_init__(self) -> None:
        """Refuse a cell size of nothing, edges off the sky, or ragged hour bands."""
        whole("propensity.min_cell", self.min_cell, (1, _MAX_MIN_CELL))
        for edge in self.elevation_bands_deg:
            if not 0 < number("propensity.elevation_bands_deg", edge) < _ZENITH_DEG:
                message = f"propensity.elevation_bands_deg edge {edge} is outside 0..90"
                raise LabelConfigError(message)
        _rising("propensity.elevation_bands_deg", self.elevation_bands_deg)
        whole("propensity.hour_band_h", self.hour_band_h, (1, _HOURS))
        if _HOURS % self.hour_band_h:
            message = f"propensity.hour_band_h = {self.hour_band_h} does not divide 24"
            raise LabelConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, for the manifest."""
        return {
            "min_cell": self.min_cell,
            "elevation_bands_deg": list(self.elevation_bands_deg),
            "hour_band_h": self.hour_band_h,
        }


def parse_completeness(value: object) -> CompletenessConfig:
    """The ``[completeness]`` table, strictly: known keys, ratios as floats."""
    values = table("completeness", value, _COMPLETENESS)
    default = CompletenessConfig()
    return CompletenessConfig(
        threshold=number(
            "completeness.threshold", values.get("threshold", default.threshold)
        ),
        sensitivity=_numbers(
            "completeness.sensitivity",
            values.get("sensitivity", list(default.sensitivity)),
        ),
        archive_min_elevation_deg=number(
            "completeness.archive_min_elevation_deg",
            values.get("archive_min_elevation_deg", default.archive_min_elevation_deg),
        ),
        archive_match_tolerance_s=whole(
            "completeness.archive_match_tolerance_s",
            values.get("archive_match_tolerance_s", default.archive_match_tolerance_s),
            (0, _MAX_TOLERANCE_S),
        ),
    )


def parse_propensity(value: object) -> PropensityConfig:
    """The ``[propensity]`` table, strictly: known keys, edges as floats."""
    values = table("propensity", value, _PROPENSITY)
    default = PropensityConfig()
    return PropensityConfig(
        min_cell=whole(
            "propensity.min_cell",
            values.get("min_cell", default.min_cell),
            (1, _MAX_MIN_CELL),
        ),
        elevation_bands_deg=_numbers(
            "propensity.elevation_bands_deg",
            values.get("elevation_bands_deg", list(default.elevation_bands_deg)),
        ),
        hour_band_h=whole(
            "propensity.hour_band_h",
            values.get("hour_band_h", default.hour_band_h),
            (1, _HOURS),
        ),
    )


def _numbers(name: str, value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        message = f"{name} must be a list, not {value!r}"
        raise LabelConfigError(message)
    return tuple(number(name, one) for one in value)


def _rising(name: str, values: tuple[float, ...]) -> None:
    if list(values) != sorted(set(values)):
        message = f"{name} must rise, with no repeats"
        raise LabelConfigError(message)

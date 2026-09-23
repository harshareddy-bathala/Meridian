"""The labelling configuration: the numbers D-146 to D-151 leave open.

Labelling is a pure function of a raw snapshot and this configuration, so the
configuration is half of what an evaluation dataset's hash depends on. Each
value has a default recorded in the decision that introduced it. Three are
top-level keys:

* ``settle_margin_s`` — how long after a pass's window closes before an absent
  report is labelled as absence rather than excluded as still on its way. A
  station's durable queue can hold a report across an outage (D-146).
* ``silent_window_s`` — how far either side of a pass another reception of the
  same satellite still counts as evidence of whether it was transmitting.
* ``silent_min_attempts`` — how many contemporaneous attempts that heard
  nothing it takes to call a satellite silent rather than indeterminate
  (D-147).

The ``[completeness]`` table holds what D-150 and D-151 leave open:

* ``threshold`` — the completeness at or above which a station-day is kept
  for primary evaluation, ``0.8``;
* ``sensitivity`` — the thresholds every report also states its counts at;
* ``archive_min_elevation_deg`` — the floor an archive station's computed pass
  must reach to count as available, applied to its peak;
* ``archive_match_tolerance_s`` — how far outside a computed window an archive
  reception may start and still be that pass, because the station's clock and
  elements are not ours.

**Strict.** An unknown key, a wrong type or a value out of range is refused
rather than ignored: a misspelled ``settle_margin_s`` that silently kept the
default is a margin somebody believes they changed, and a dataset whose hash
says it was made under settings it was not.

**The hash is of the values, not the file.** :func:`config_sha256` renders the
resolved configuration canonically, so a comment or a reordered key does not
change a dataset's hash, and an absent file and one spelling out the defaults
give the same one — they produce the same labels, which is what the hash is a
promise about. D-144 says "the sha256 of the configuration file"; this is that,
taken over the only part of the file that can change a label.

Reference: docs/DECISIONS.md D-144, D-146, D-147, D-150, D-151.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "CompletenessConfig",
    "LabelConfig",
    "LabelConfigError",
    "config_sha256",
    "load_label_config",
    "parse_label_config",
]

_DAY_S = 86_400
_LIMITS: dict[str, tuple[int, int]] = {
    "settle_margin_s": (0, 30 * _DAY_S),
    "silent_window_s": (1, 7 * _DAY_S),
    "silent_min_attempts": (1, 100),
}
"""Inclusive bounds. A thirty-day margin is already a snapshot that labels
nothing recent; a week either side is already not "contemporaneous"."""


_COMPLETENESS = frozenset(
    (
        "threshold",
        "sensitivity",
        "archive_min_elevation_deg",
        "archive_match_tolerance_s",
    )
)
_MAX_TOLERANCE_S = 3600
"""An hour either side is already a different pass of most LEO satellites."""


class LabelConfigError(ValueError):
    """A labelling configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class CompletenessConfig:
    """The ``[completeness]`` table (D-150, D-151)."""

    threshold: float = 0.8
    sensitivity: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9)
    archive_min_elevation_deg: float = 0.0
    archive_match_tolerance_s: int = 120

    def __post_init__(self) -> None:
        """Refuse a ratio outside 0..1, an unsorted list, or a floor off the sky."""
        _ratio("completeness.threshold", self.threshold)
        if not self.sensitivity:
            message = "completeness.sensitivity must name at least one threshold"
            raise LabelConfigError(message)
        for one in self.sensitivity:
            _ratio("completeness.sensitivity", one)
        if list(self.sensitivity) != sorted(set(self.sensitivity)):
            message = "completeness.sensitivity must rise, with no repeats"
            raise LabelConfigError(message)
        _number(
            "completeness.archive_min_elevation_deg", self.archive_min_elevation_deg
        )
        if not 0 <= self.archive_min_elevation_deg < 90:  # noqa: PLR2004 — zenith
            message = "completeness.archive_min_elevation_deg is outside 0..90"
            raise LabelConfigError(message)
        _whole(
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
class LabelConfig:
    """The resolved labelling configuration."""

    settle_margin_s: int = _DAY_S
    silent_window_s: int = _DAY_S // 2
    silent_min_attempts: int = 2
    completeness: CompletenessConfig = field(default_factory=CompletenessConfig)

    def __post_init__(self) -> None:
        """Refuse a value outside its bounds, or one that is not an integer."""
        for name, limits in _LIMITS.items():
            _whole(name, getattr(self, name), limits)

    def parameters(self) -> dict[str, object]:
        """The values, for the manifest, where a reader sees them beside the hash."""
        return {name: getattr(self, name) for name in _LIMITS} | {
            "completeness": self.completeness.parameters()
        }


def parse_label_config(text: str) -> LabelConfig:
    """Read a labelling configuration from TOML text.

    Args:
        text: The file's contents. Every key is optional.

    Returns:
        The resolved configuration, defaults filled in.

    Raises:
        LabelConfigError: Not TOML, an unknown key, or a value that is not a
            whole number inside its bounds.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"the labelling configuration is not TOML: {exc}"
        raise LabelConfigError(message) from exc
    known = {*_LIMITS, "completeness"}
    unknown = sorted(set(stored) - known)
    if unknown:
        message = f"unknown labelling settings {unknown}; known: {sorted(known)}"
        raise LabelConfigError(message)
    table = stored.pop("completeness", {})
    return LabelConfig(**stored, completeness=_completeness(table))


def _completeness(table: object) -> CompletenessConfig:
    """The ``[completeness]`` table, strictly: a table, known keys, ratios as floats."""
    if not isinstance(table, Mapping):
        message = "completeness must be a table, [completeness]"
        raise LabelConfigError(message)
    unknown = sorted(set(table) - _COMPLETENESS)
    if unknown:
        message = (
            f"unknown completeness settings {unknown}; known: {sorted(_COMPLETENESS)}"
        )
        raise LabelConfigError(message)
    values = dict(table)
    if "sensitivity" in values:
        listed = values["sensitivity"]
        if not isinstance(listed, list):
            message = f"completeness.sensitivity must be a list, not {listed!r}"
            raise LabelConfigError(message)
        values["sensitivity"] = tuple(
            _number("completeness.sensitivity", one) for one in listed
        )
    for name in ("threshold", "archive_min_elevation_deg"):
        if name in values:
            values[name] = _number(f"completeness.{name}", values[name])
    return CompletenessConfig(**values)


def _number(name: str, value: object) -> float:
    """A TOML integer or float as a float; a boolean or text is refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} must be a number, not {value!r}"
        raise LabelConfigError(message)
    return float(value)


def _whole(name: str, value: object, limits: tuple[int, int]) -> None:
    """Refuse anything but a whole number inside the inclusive ``limits``."""
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} must be a whole number, not {value!r}"
        raise LabelConfigError(message)
    low, high = limits
    if not low <= value <= high:
        message = f"{name} = {value} is outside {low}..{high}"
        raise LabelConfigError(message)


def _ratio(name: str, value: object) -> None:
    if not 0 <= _number(name, value) <= 1:
        message = f"{name} = {value} is outside 0..1"
        raise LabelConfigError(message)


def load_label_config(path: Path | None) -> LabelConfig:
    """Read a labelling configuration file, or take the defaults.

    Args:
        path: The file, or None for the defaults.

    Returns:
        The resolved configuration.

    Raises:
        LabelConfigError: The file cannot be read, or :func:`parse_label_config`
            refuses it.
    """
    if path is None:
        return LabelConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"cannot read {path}: {exc}"
        raise LabelConfigError(message) from exc
    return parse_label_config(text)


def config_sha256(config: LabelConfig) -> bytes:
    """The hash an evaluation dataset records for the configuration it used.

    Args:
        config: The resolved configuration.

    Returns:
        The sha256 of its values rendered canonically.
    """
    return hashlib.sha256(canonical_bytes(config.parameters())).digest()

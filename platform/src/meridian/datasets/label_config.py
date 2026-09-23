"""The labelling configuration: the numbers D-146 to D-152 leave open.

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

Two tables hold the selection-bias settings of Stage 16: ``[completeness]``
(D-150, D-151) and ``[propensity]`` (D-152), described in
:mod:`meridian.datasets.selection_config`.

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

Reference: docs/DECISIONS.md D-144, D-146, D-147, D-150, D-151, D-152.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.config_checks import LabelConfigError, whole
from meridian.datasets.selection_config import (
    CompletenessConfig,
    PropensityConfig,
    parse_completeness,
    parse_propensity,
)

__all__ = [
    "CompletenessConfig",
    "LabelConfig",
    "LabelConfigError",
    "PropensityConfig",
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


@dataclass(frozen=True, slots=True)
class LabelConfig:
    """The resolved labelling configuration."""

    settle_margin_s: int = _DAY_S
    silent_window_s: int = _DAY_S // 2
    silent_min_attempts: int = 2
    completeness: CompletenessConfig = field(default_factory=CompletenessConfig)
    propensity: PropensityConfig = field(default_factory=PropensityConfig)

    def __post_init__(self) -> None:
        """Refuse a value outside its bounds, or one that is not an integer."""
        for name, limits in _LIMITS.items():
            whole(name, getattr(self, name), limits)

    def parameters(self) -> dict[str, object]:
        """The values, for the manifest, where a reader sees them beside the hash."""
        return {name: getattr(self, name) for name in _LIMITS} | {
            "completeness": self.completeness.parameters(),
            "propensity": self.propensity.parameters(),
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
    known = {*_LIMITS, "completeness", "propensity"}
    unknown = sorted(set(stored) - known)
    if unknown:
        message = f"unknown labelling settings {unknown}; known: {sorted(known)}"
        raise LabelConfigError(message)
    completeness = parse_completeness(stored.pop("completeness", {}))
    propensity = parse_propensity(stored.pop("propensity", {}))
    return LabelConfig(**stored, completeness=completeness, propensity=propensity)


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

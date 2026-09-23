"""The labelling configuration: the three numbers D-146 and D-147 leave open.

Labelling is a pure function of a raw snapshot and this configuration, so the
configuration is half of what an evaluation dataset's hash depends on. Three
values, each with a default recorded in the decision that introduced it:

* ``settle_margin_s`` — how long after a pass's window closes before an absent
  report is labelled as absence rather than excluded as still on its way. A
  station's durable queue can hold a report across an outage (D-146).
* ``silent_window_s`` — how far either side of a pass another reception of the
  same satellite still counts as evidence of whether it was transmitting.
* ``silent_min_attempts`` — how many contemporaneous attempts that heard
  nothing it takes to call a satellite silent rather than indeterminate
  (D-147).

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

Reference: docs/DECISIONS.md D-144, D-146, D-147.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
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


class LabelConfigError(ValueError):
    """A labelling configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class LabelConfig:
    """The resolved labelling configuration."""

    settle_margin_s: int = _DAY_S
    silent_window_s: int = _DAY_S // 2
    silent_min_attempts: int = 2

    def __post_init__(self) -> None:
        """Refuse a value outside its bounds, or one that is not an integer."""
        for name, (low, high) in _LIMITS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                message = f"{name} must be a whole number, not {value!r}"
                raise LabelConfigError(message)
            if not low <= value <= high:
                message = f"{name} = {value} is outside {low}..{high}"
                raise LabelConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, for the manifest, where a reader sees them beside the hash."""
        return {name: getattr(self, name) for name in _LIMITS}


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
    unknown = sorted(set(stored) - set(_LIMITS))
    if unknown:
        message = f"unknown labelling settings {unknown}; known: {sorted(_LIMITS)}"
        raise LabelConfigError(message)
    return LabelConfig(**stored)


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

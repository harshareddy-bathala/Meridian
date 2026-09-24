"""How every labelling setting is checked: strictly, and by name.

A misspelled key that silently kept its default is a setting somebody believes
they changed, and a dataset whose hash says it was made under settings it was
not. So each check here refuses rather than coerces, and names the setting in
its message. They are shared by :mod:`meridian.datasets.label_config` and
:mod:`meridian.datasets.selection_config`, so both tables fail the same way.

Reference: docs/DECISIONS.md D-144.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "LabelConfigError",
    "number",
    "ratio",
    "table",
    "whole",
]


class LabelConfigError(ValueError):
    """A labelling configuration that cannot be obeyed as written."""


def number(name: str, value: object) -> float:
    """A TOML integer or float as a float; a boolean or text is refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} must be a number, not {value!r}"
        raise LabelConfigError(message)
    return float(value)


def whole(name: str, value: object, limits: tuple[int, int]) -> int:
    """A whole number inside the inclusive ``limits``, or a refusal."""
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} must be a whole number, not {value!r}"
        raise LabelConfigError(message)
    low, high = limits
    if not low <= value <= high:
        message = f"{name} = {value} is outside {low}..{high}"
        raise LabelConfigError(message)
    return value


def ratio(name: str, value: object) -> None:
    """Refuse anything but a number from 0 to 1."""
    if not 0 <= number(name, value) <= 1:
        message = f"{name} = {value} is outside 0..1"
        raise LabelConfigError(message)


def table(name: str, value: object, known: frozenset[str]) -> dict[str, object]:
    """A TOML table with only ``known`` keys, as a dict to be filled in."""
    if not isinstance(value, Mapping):
        message = f"{name} must be a table, [{name}]"
        raise LabelConfigError(message)
    unknown = sorted(set(value) - known)
    if unknown:
        message = f"unknown {name} settings {unknown}; known: {sorted(known)}"
        raise LabelConfigError(message)
    return {str(key): held for key, held in value.items()}

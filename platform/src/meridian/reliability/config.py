"""What the reliability layer classifies under, and the targets it reports against.

Two tables, read from ``deploy/reliability.toml`` or taken as defaults:

* ``[classification]`` — the margins a pass is classified under. Every stored
  classification records the sha256 of these, so a figure names what it was
  counted under, and changing one writes new rows beside the old (D-182). The
  defaults are the snapshot labeller's (D-146, D-147).
* ``[slo]`` — the window every figure is counted over, and the target each is
  judged against (D-184). Changing a target changes no classification, so it
  is not in the hash.

**Strict**, as the labelling configuration is: an unknown key, a wrong type or
a value out of range is refused by name, because a misspelled target that
silently kept its default is a target somebody believes they set.

Standard library only: the snapshot command reads it beside the labeller.

Reference: docs/DECISIONS.md D-146, D-147, D-182, D-184.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TypeVar

__all__ = [
    "ClassificationConfig",
    "ReliabilityConfig",
    "ReliabilityConfigError",
    "SloConfig",
    "load_reliability_config",
    "parse_reliability_config",
]

_DAY_S = 86_400


class ReliabilityConfigError(ValueError):
    """The reliability configuration cannot be read, or holds a bad value."""


def _whole(name: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReliabilityConfigError(f"{name} must be a whole number, not {value!r}")
    if not low <= value <= high:
        raise ReliabilityConfigError(f"{name} must be in {low}..{high}, not {value}")


def _share(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReliabilityConfigError(f"{name} must be a number, not {value!r}")
    if not 0.0 < value < 1.0:
        raise ReliabilityConfigError(f"{name} must be between 0 and 1, not {value}")


@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    """The margins the live accounting classifies under."""

    settle_margin_s: int = _DAY_S
    """How long after a window closes its pass waits to be classified. A
    station queues reports through an outage, and without a margin a report
    still on its way would be read as absence (D-146)."""

    silent_window_s: int = _DAY_S // 2
    """How far either side of a pass another reception still counts as
    evidence about the satellite (D-147)."""

    silent_min_attempts: int = 2
    """Confirmed silences needed to call a satellite silent (D-147)."""

    def __post_init__(self) -> None:
        """Refuse a value outside the labeller's own bounds."""
        _whole("settle_margin_s", self.settle_margin_s, 0, 30 * _DAY_S)
        _whole("silent_window_s", self.silent_window_s, 1, 7 * _DAY_S)
        _whole("silent_min_attempts", self.silent_min_attempts, 1, 100)

    def parameters(self) -> dict[str, int]:
        """The parameters as recorded with every classification."""
        return asdict(self)

    def sha256(self) -> bytes:
        """The hash stored beside every classification made under these."""
        text = json.dumps(self.parameters(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class SloConfig:
    """The window figures are counted over, and each figure's target (D-184).

    SC-4 and SC-5 are the project's claims (``EVALUATION.md`` §1). The other
    targets are proposed, to agree with the team, and every report says which
    is which.
    """

    window_days: int = 30
    """SC-4 is measured over 30 days."""

    capture_rate_min: float = 0.90
    """SC-4: ≥ 90% pass capture rate. Also sets the loss budget (D-185)."""

    confirmed_miss_rate_max: float = 0.05
    station_availability_min: float = 0.95
    assignment_completion_rate_min: float = 0.95
    schedule_execution_rate_min: float = 0.90
    submission_delay_p95_max_s: int = 3_600

    failure_detection_max_s: int = 90
    """SC-5: ≤ 90 s to detect a failure."""

    def __post_init__(self) -> None:
        """Refuse a target that cannot be met or cannot be missed."""
        _whole("window_days", self.window_days, 1, 366)
        for name in (
            "capture_rate_min",
            "confirmed_miss_rate_max",
            "station_availability_min",
            "assignment_completion_rate_min",
            "schedule_execution_rate_min",
        ):
            _share(name, getattr(self, name))
        _whole("submission_delay_p95_max_s", self.submission_delay_p95_max_s, 1, _DAY_S)
        _whole("failure_detection_max_s", self.failure_detection_max_s, 1, 3_600)

    def parameters(self) -> dict[str, float]:
        """The targets, as every report prints them."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReliabilityConfig:
    """Both tables, resolved."""

    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    slo: SloConfig = field(default_factory=SloConfig)


_TABLES = ("classification", "slo")

_T = TypeVar("_T", ClassificationConfig, SloConfig)


def parse_reliability_config(text: str) -> ReliabilityConfig:
    """Read a reliability configuration from TOML text.

    Args:
        text: The file's contents. Every table and key is optional.

    Returns:
        The resolved configuration, defaults filled in.

    Raises:
        ReliabilityConfigError: Not TOML, an unknown table or key, or a value
            out of range.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ReliabilityConfigError(f"not TOML: {exc}") from exc
    unknown = sorted(set(stored) - set(_TABLES))
    if unknown:
        raise ReliabilityConfigError(
            f"unknown tables {unknown}; known: {sorted(_TABLES)}"
        )
    return ReliabilityConfig(
        classification=_table(stored, "classification", ClassificationConfig),
        slo=_table(stored, "slo", SloConfig),
    )


def _table(stored: dict[str, object], name: str, kind: type[_T]) -> _T:
    """One table, every key known, built into its dataclass."""
    table = stored.get(name, {})
    if not isinstance(table, dict):
        raise ReliabilityConfigError(f"[{name}] must be a table")
    known = {one.name for one in fields(kind)}
    extra = sorted(set(table) - known)
    if extra:
        raise ReliabilityConfigError(
            f"unknown settings {extra} in [{name}]; known: {sorted(known)}"
        )
    return kind(**table)


def load_reliability_config(path: Path | None) -> ReliabilityConfig:
    """Read a reliability configuration file, or take the defaults.

    Raises:
        ReliabilityConfigError: The file cannot be read, or is refused.
    """
    if path is None:
        return ReliabilityConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReliabilityConfigError(f"cannot read {path}: {exc}") from exc
    return parse_reliability_config(text)

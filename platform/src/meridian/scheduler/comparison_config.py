"""The retrospective comparison's configuration — which models, and how to solve.

One file, ``schedule-evaluation.toml``, holds everything a comparison's
numbers depend on besides the dataset, so a comparison is regenerable from a
dataset, this file and its seed (rule 8, D-172):

* ``[models]`` — the model directories of ``A``, ``C`` and ``D``, as paths
  under the datasets root or absolute ones. All three are required, fitted on
  the dataset compared, with one population and one pair of split dates. B
  names none: its model is A's (D-160);
* ``frames``, ``time_limit_s``, ``turnaround_s`` and ``seed`` — as in
  ``schedule.toml``, and read by the same checks. The time limit is per
  station-day, and every scheduler has the same one;
* ``resamples`` — how many bootstrap resamples the interval is drawn from.
  Default 2000;
* ``threshold`` — a completeness threshold in place of the dataset's own, to
  read the comparison at another (D-151). Optional.

Strict, as the other configurations are, and hashed over resolved values —
the model paths left out, since the report names each model by its own hash.

Reference: docs/DECISIONS.md D-151, D-160, D-168, D-172.
"""

from __future__ import annotations

import hashlib
import json
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from meridian.prediction.replay import MODELLED
from meridian.scheduler.schedule_config import ScheduleConfig, ScheduleConfigError

__all__ = [
    "ComparisonConfig",
    "comparison_config_sha256",
    "load_comparison_config",
    "parse_comparison_config",
]

_SOLVING = ("frames", "time_limit_s", "turnaround_s", "seed")
_OWN = ("models", "resamples", "threshold")
_FEWEST_RESAMPLES = 100
_MOST_RESAMPLES = 100_000


@dataclass(frozen=True, slots=True)
class ComparisonConfig:
    """The resolved comparison configuration."""

    models: Mapping[str, str]
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    """The frames term, time limit, turnaround and seed; its configuration and
    model are not read, since every configuration is run."""

    resamples: int = 2000
    threshold: float | None = None

    def __post_init__(self) -> None:
        """Refuse a model list, resample count or threshold that is not one."""
        if sorted(self.models) != list(MODELLED) or not all(
            isinstance(one, str) and one.strip() for one in self.models.values()
        ):
            message = (
                f"[models] names the directories of {', '.join(MODELLED)}, each"
                f" a path; B's model is A's. Given: {dict(self.models)!r}"
            )
            raise ScheduleConfigError(message)
        if (
            isinstance(self.resamples, bool)
            or not isinstance(self.resamples, int)
            or not _FEWEST_RESAMPLES <= self.resamples <= _MOST_RESAMPLES
        ):
            message = (
                f"resamples must be a whole number from {_FEWEST_RESAMPLES} to"
                f" {_MOST_RESAMPLES}, not {self.resamples!r}"
            )
            raise ScheduleConfigError(message)
        if self.threshold is not None and (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, int | float)
            or not math.isfinite(self.threshold)
            or not 0 < self.threshold <= 1
        ):
            message = f"threshold must be above 0 and at most 1, not {self.threshold!r}"
            raise ScheduleConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values the numbers depend on, as the report states them.

        Not the model paths: where a model is kept is not what it is, and the
        report names each model by its hash instead.
        """
        solving = self.schedule.parameters()
        return {
            **{name: solving[name] for name in _SOLVING},
            "resamples": self.resamples,
            "threshold": None if self.threshold is None else float(self.threshold),
        }

    def model_paths(self, root: Path) -> dict[str, Path]:
        """Each model's directory: under ``root`` unless the path is absolute."""
        return {name: root / path for name, path in sorted(self.models.items())}


def parse_comparison_config(text: str) -> ComparisonConfig:
    """Read a comparison configuration from TOML text.

    Raises:
        ScheduleConfigError: Not TOML, an unknown key, or a value refused.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"the comparison configuration is not TOML: {exc}"
        raise ScheduleConfigError(message) from exc
    known = {*_SOLVING, *_OWN}
    unknown = sorted(set(stored) - known)
    if unknown:
        message = f"unknown comparison settings {unknown}; known: {sorted(known)}"
        raise ScheduleConfigError(message)
    models = stored.get("models", {})
    if not isinstance(models, dict):
        message = f"[models] must be a table, not {models!r}"
        raise ScheduleConfigError(message)
    return ComparisonConfig(
        models=models,
        schedule=ScheduleConfig(**{k: v for k, v in stored.items() if k in _SOLVING}),
        **{k: v for k, v in stored.items() if k in ("resamples", "threshold")},
    )


def load_comparison_config(path: Path) -> ComparisonConfig:
    """Read a comparison configuration file.

    Raises:
        ScheduleConfigError: The file cannot be read, or is refused.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"cannot read the comparison configuration {path}: {exc.strerror}"
        raise ScheduleConfigError(message) from exc
    return parse_comparison_config(text)


def comparison_config_sha256(config: ComparisonConfig) -> bytes:
    """The configuration's hash, over its resolved values."""
    text = json.dumps(config.parameters(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).digest()

"""The schedule configuration — which objective, from which model, how long to solve.

One file, ``schedule.toml``, chooses everything a scheduler run decides by, so
changing configuration is an edit to it, never to code (D-160, D-168):

* ``configuration`` — ``"A"`` to ``"D"``. Default ``"A"``, the one that runs
  with no model.
* ``model`` — a published model directory, as a path under the datasets root
  or an absolute one. Optional for A and B, which take the elevation proxy
  without one; required for C and D. A model must be its configuration's: C's
  for C, D's for D, and A's for A or B, since B's model is A's (D-160).
* ``frames`` — ``"duration"`` or ``"none"`` (D-168). Default ``"duration"``.
* ``time_limit_s`` — the solver's wall-clock limit (D-167). Default 10.
* ``turnaround_s`` — seconds a station needs between two receptions (D-066).
  Default 0: Phase 1's stations receive on a fixed antenna that does not slew.
* ``seed`` — handed to the solver. Default 0.

A model that reads history (C, D) takes it from the newest labelled dataset
under the datasets root, and the run states that history's ``as_of`` (D-169).
The dataset is found, not named: history has to stay current, and a name in a
file would go stale the day a newer dataset is labelled.

**Strict, as the model configuration is:** an unknown key, a wrong type or a
value out of range is refused by name, and the hash is of the resolved values,
so a comment or a reordered key does not change it.

Reference: docs/DECISIONS.md D-066, D-160, D-167, D-168, D-169.
"""

from __future__ import annotations

import hashlib
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from meridian.scheduler.objective import FRAMES

__all__ = [
    "CONFIGURATIONS",
    "ScheduleConfig",
    "ScheduleConfigError",
    "check_model",
    "load_schedule_config",
    "model_path",
    "parse_schedule_config",
    "schedule_config_sha256",
]

CONFIGURATIONS = ("A", "B", "C", "D")

MODELS_FOR = {"A": ("A", "B"), "B": ("A", "B"), "C": ("C",), "D": ("D",)}
"""The model configurations each schedule configuration may score with."""

_NEEDS_A_MODEL = ("C", "D")
_MOST_SECONDS = 3600.0
_MAX_SEED = 2**31 - 1
"""HiGHS's ``random_seed`` is a 32-bit signed integer."""


class ScheduleConfigError(ValueError):
    """A schedule configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    """The resolved schedule configuration."""

    configuration: str = "A"
    model: str | None = None
    frames: str = "duration"
    time_limit_s: float = 10.0
    turnaround_s: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        """Refuse a choice that is not one, or a number off the scale."""
        _one_of("configuration", self.configuration, CONFIGURATIONS)
        _one_of("frames", self.frames, FRAMES)
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model.strip()
        ):
            message = f"model must be a directory's path, not {self.model!r}"
            raise ScheduleConfigError(message)
        if self.model is None and self.configuration in _NEEDS_A_MODEL:
            message = (
                f"configuration {self.configuration} is a learned model: name"
                " its model directory as `model`; only A and B run without one"
            )
            raise ScheduleConfigError(message)
        _seconds("time_limit_s", self.time_limit_s, positive=True)
        _seconds("turnaround_s", self.turnaround_s, positive=False)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            message = f"seed must be a whole number, not {self.seed!r}"
            raise ScheduleConfigError(message)
        if not 0 <= self.seed <= _MAX_SEED:
            message = f"seed = {self.seed} is outside 0..{_MAX_SEED}"
            raise ScheduleConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, as a run records them."""
        return {
            "configuration": self.configuration,
            "model": self.model,
            "frames": self.frames,
            "time_limit_s": float(self.time_limit_s),
            "turnaround_s": float(self.turnaround_s),
            "seed": self.seed,
        }


def parse_schedule_config(text: str) -> ScheduleConfig:
    """Read a schedule configuration from TOML text.

    Raises:
        ScheduleConfigError: Not TOML, an unknown key, or a value refused.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"the schedule configuration is not TOML: {exc}"
        raise ScheduleConfigError(message) from exc
    known = set(ScheduleConfig.__dataclass_fields__)
    unknown = sorted(set(stored) - known)
    if unknown:
        message = f"unknown schedule settings {unknown}; known: {sorted(known)}"
        raise ScheduleConfigError(message)
    return ScheduleConfig(**stored)


def load_schedule_config(path: Path | None) -> ScheduleConfig:
    """Read a schedule configuration file, or take the defaults.

    Raises:
        ScheduleConfigError: The file cannot be read, or is refused.
    """
    if path is None:
        return ScheduleConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"cannot read the schedule configuration {path}: {exc.strerror}"
        raise ScheduleConfigError(message) from exc
    return parse_schedule_config(text)


def schedule_config_sha256(config: ScheduleConfig) -> bytes:
    """The configuration's hash, over its resolved values."""
    text = json.dumps(config.parameters(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).digest()


def model_path(config: ScheduleConfig, root: Path) -> Path | None:
    """Where the configured model is: under ``root`` unless the path is absolute.

    Joining an absolute path to ``root`` gives the absolute path, so one
    expression serves both.
    """
    return None if config.model is None else root / config.model


def check_model(config: ScheduleConfig, model_configuration: str) -> None:
    """Refuse a model fitted for another configuration.

    Raises:
        ScheduleConfigError: C scored by D's model, say, would be labelled C
            and be D.
    """
    allowed = MODELS_FOR[config.configuration]
    if model_configuration not in allowed:
        message = (
            f"configuration {config.configuration} is scored by a model of"
            f" configuration {' or '.join(allowed)}, and {config.model} is"
            f" configuration {model_configuration}"
        )
        raise ScheduleConfigError(message)


def _one_of(name: str, value: object, choices: tuple[str, ...]) -> None:
    if value not in choices:
        message = f"{name} must be one of {list(choices)}, not {value!r}"
        raise ScheduleConfigError(message)


def _seconds(name: str, value: object, *, positive: bool) -> None:
    """A finite number of seconds, at most an hour, positive or non-negative."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value > _MOST_SECONDS
        or value < 0
        or (positive and value == 0)
    ):
        bound = "above 0" if positive else "at least 0"
        message = f"{name} must be seconds {bound} and at most 3600, not {value!r}"
        raise ScheduleConfigError(message)

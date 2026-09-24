"""The model configuration — which of A to D, on whose passes, from one file.

D-160 makes the configuration the only way to choose a model's inputs: the
four configurations run through one code path, and changing from one to
another is an edit to this file, never to code.

* ``configuration`` — ``"A"``, ``"B"``, ``"C"`` or ``"D"`` (D-160). Default
  ``"D"``, the shipped system.
* ``population`` — ``"own"`` or ``"archive"`` (D-156). Default ``"own"``. The
  two are never pooled, and an archive pass carries its peak elevation and
  nothing else we can compute features from, so ``"archive"`` is refused with
  any configuration but A.
* ``min_station_history`` — how many settled, usable outcomes a station needs
  before the configured model scores it; below that, the geometry-only model
  does (D-161). Default 20.
* ``train_until`` and ``validate_until`` — the split dates (D-162). A pass
  rising before the first trains, before the second validates, and after it is
  test. No default: a fit refuses a configuration that does not name them,
  since a date chosen for you is a date nobody checked against the data.
* ``inverse_regularisation`` — scikit-learn's ``C`` for the L2 penalty
  (D-155). Default 1.0. Stated, not tuned: nothing is searched over, so no
  choice can reach the test span.
* ``weighting`` — ``"none"`` or ``"ipw"`` (D-156). Default ``"none"``.
* ``seed`` — recorded with the model and handed to the solver. Default 0.

**Strict, as the labelling configuration is:** an unknown key, a wrong type or
a value out of range is refused by name, and the hash is of the resolved
values, so a comment or a reordered key does not change it.

Reference: docs/DECISIONS.md D-144, D-155, D-156, D-160, D-161, D-162.
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "CONFIGURATION_NAMES",
    "POPULATIONS",
    "WEIGHTINGS",
    "ModelConfig",
    "ModelConfigError",
    "load_model_config",
    "model_config_sha256",
    "parse_model_config",
]

CONFIGURATION_NAMES = ("A", "B", "C", "D")
POPULATIONS = ("own", "archive")
WEIGHTINGS = ("none", "ipw")
_ARCHIVE_ONLY = ("A",)
"""What an archive pass has features for: its peak elevation."""

_MAX_HISTORY = 10_000
_MAX_SEED = 2**32 - 1


class ModelConfigError(ValueError):
    """A model configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """The resolved model configuration."""

    configuration: str = "D"
    population: str = "own"
    min_station_history: int = 20
    train_until: datetime | None = None
    validate_until: datetime | None = None
    inverse_regularisation: float = 1.0
    weighting: str = "none"
    seed: int = 0

    def __post_init__(self) -> None:
        """Refuse a name that is not one of the choices, or a value off the scale."""
        _one_of("configuration", self.configuration, CONFIGURATION_NAMES)
        _one_of("population", self.population, POPULATIONS)
        _one_of("weighting", self.weighting, WEIGHTINGS)
        if self.population == "archive" and self.configuration not in _ARCHIVE_ONLY:
            message = (
                f"configuration {self.configuration} cannot be fitted on the archive:"
                " an archive pass carries its peak elevation and nothing else we"
                " compute features from, so only configuration A can (D-156)"
            )
            raise ModelConfigError(message)
        _whole("min_station_history", self.min_station_history, _MAX_HISTORY)
        _whole("seed", self.seed, _MAX_SEED)
        strength = self.inverse_regularisation
        if (
            isinstance(strength, bool)
            or not isinstance(strength, int | float)
            or not math.isfinite(strength)
            or strength <= 0
        ):
            message = (
                f"inverse_regularisation must be a positive number, not {strength!r}"
            )
            raise ModelConfigError(message)
        self._check_dates()

    def _check_dates(self) -> None:
        """Each date aware, and training ending before validation does."""
        for name in ("train_until", "validate_until"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, datetime) or value.tzinfo is None
            ):
                message = f"{name} must be a date-time with a UTC offset, not {value!r}"
                raise ModelConfigError(message)
        if (
            self.train_until is not None
            and self.validate_until is not None
            and self.train_until >= self.validate_until
        ):
            message = (
                f"train_until {self.train_until.isoformat()} is not before"
                f" validate_until {self.validate_until.isoformat()}"
            )
            raise ModelConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, for a model's manifest."""
        return {
            "configuration": self.configuration,
            "population": self.population,
            "min_station_history": self.min_station_history,
            "train_until": self.train_until,
            "validate_until": self.validate_until,
            "inverse_regularisation": float(self.inverse_regularisation),
            "weighting": self.weighting,
            "seed": self.seed,
        }


def parse_model_config(text: str) -> ModelConfig:
    """Read a model configuration from TOML text.

    Args:
        text: The file's contents. Every key is optional.

    Returns:
        The resolved configuration, defaults filled in.

    Raises:
        ModelConfigError: Not TOML, an unknown key, or a value that cannot be
            obeyed.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"the model configuration is not TOML: {exc}"
        raise ModelConfigError(message) from exc
    known = set(ModelConfig.__dataclass_fields__)
    unknown = sorted(set(stored) - known)
    if unknown:
        message = f"unknown model settings {unknown}; known: {sorted(known)}"
        raise ModelConfigError(message)
    return ModelConfig(**stored)


def load_model_config(path: Path | None) -> ModelConfig:
    """Read a model configuration file, or take the defaults.

    Raises:
        ModelConfigError: The file cannot be read, or is refused.
    """
    if path is None:
        return ModelConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"cannot read the model configuration {path}: {exc.strerror}"
        raise ModelConfigError(message) from exc
    return parse_model_config(text)


def model_config_sha256(config: ModelConfig) -> bytes:
    """The configuration's hash, over its resolved values."""
    return hashlib.sha256(canonical_bytes(config.parameters())).digest()


def _whole(name: str, value: object, largest: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} must be a whole number, not {value!r}"
        raise ModelConfigError(message)
    if not 0 <= value <= largest:
        message = f"{name} = {value} is outside 0..{largest}"
        raise ModelConfigError(message)


def _one_of(name: str, value: object, choices: tuple[str, ...]) -> None:
    if value not in choices:
        message = f"{name} must be one of {list(choices)}, not {value!r}"
        raise ModelConfigError(message)

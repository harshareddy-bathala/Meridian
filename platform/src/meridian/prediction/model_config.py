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

**Strict, as the labelling configuration is:** an unknown key, a wrong type or
a value out of range is refused by name, and the hash is of the resolved
values, so a comment or a reordered key does not change it.

Reference: docs/DECISIONS.md D-144, D-156, D-160, D-161.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "CONFIGURATION_NAMES",
    "POPULATIONS",
    "ModelConfig",
    "ModelConfigError",
    "load_model_config",
    "model_config_sha256",
    "parse_model_config",
]

CONFIGURATION_NAMES = ("A", "B", "C", "D")
POPULATIONS = ("own", "archive")
_ARCHIVE_ONLY = ("A",)
"""What an archive pass has features for: its peak elevation."""

_MAX_HISTORY = 10_000


class ModelConfigError(ValueError):
    """A model configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """The resolved model configuration."""

    configuration: str = "D"
    population: str = "own"
    min_station_history: int = 20

    def __post_init__(self) -> None:
        """Refuse a name that is not one of the choices, or history off the scale."""
        _one_of("configuration", self.configuration, CONFIGURATION_NAMES)
        _one_of("population", self.population, POPULATIONS)
        if self.population == "archive" and self.configuration not in _ARCHIVE_ONLY:
            message = (
                f"configuration {self.configuration} cannot be fitted on the archive:"
                " an archive pass carries its peak elevation and nothing else we"
                " compute features from, so only configuration A can (D-156)"
            )
            raise ModelConfigError(message)
        history = self.min_station_history
        if isinstance(history, bool) or not isinstance(history, int):
            message = f"min_station_history must be a whole number, not {history!r}"
            raise ModelConfigError(message)
        if not 0 <= history <= _MAX_HISTORY:
            message = f"min_station_history = {history} is outside 0..{_MAX_HISTORY}"
            raise ModelConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, for a model's manifest."""
        return {
            "configuration": self.configuration,
            "population": self.population,
            "min_station_history": self.min_station_history,
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


def _one_of(name: str, value: object, choices: tuple[str, ...]) -> None:
    if value not in choices:
        message = f"{name} must be one of {list(choices)}, not {value!r}"
        raise ModelConfigError(message)

"""The verdict configuration: split dates, regularisation, seed, threshold, rubric.

One TOML file, ``deploy/verdict.toml.example`` documented, read strictly as the
model configuration is (D-162): an unknown key, a wrong type or a value out of
range is refused by name, and the hash is of the resolved values.

* ``train_until`` and ``validate_until`` — the split dates. A reception that
  started before the first trains, before the second calibrates, and after it
  is test. No default: a fit refuses a configuration that does not name them.
* ``inverse_regularisation`` — scikit-learn's ``C`` (D-155). Default 1.0.
  Stated, never tuned.
* ``seed`` — handed to the solver and recorded. Default 0.
* ``partial_below`` — the verdict below which a decoded reception counts as
  partial (Stage 26). Default 0.5. Chosen by looking at the calibrated
  verdict on validation, which ``meridian verdict evaluate`` prints, and then
  written here. It is recorded with every model and every run (D-262).
* ``rubric`` — which rating instructions a label must have been made under
  (D-260). Default ``usable-1``. A rating under another rubric is a different
  label, left out and counted.

Reference: docs/DECISIONS.md D-155, D-162, D-260, D-262.
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "VerdictConfig",
    "VerdictConfigError",
    "load_verdict_config",
    "parse_verdict_config",
    "verdict_config_sha256",
]

_MAX_SEED = 2**32 - 1
_RUBRIC = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")
_KEYS = (
    "train_until",
    "validate_until",
    "inverse_regularisation",
    "seed",
    "partial_below",
    "rubric",
)


class VerdictConfigError(ValueError):
    """A verdict configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class VerdictConfig:
    """The resolved verdict configuration."""

    train_until: datetime | None = None
    validate_until: datetime | None = None
    inverse_regularisation: float = 1.0
    seed: int = 0
    partial_below: float = 0.5
    rubric: str = "usable-1"

    def __post_init__(self) -> None:
        """Refuse a value of the wrong type or off its scale."""
        for name in ("train_until", "validate_until"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, datetime) or value.tzinfo is None
            ):
                message = f"{name} must be a date-time with a UTC offset, not {value!r}"
                raise VerdictConfigError(message)
        if (
            self.train_until is not None
            and self.validate_until is not None
            and self.train_until >= self.validate_until
        ):
            message = "train_until must be before validate_until"
            raise VerdictConfigError(message)
        if not _finite(self.inverse_regularisation) or self.inverse_regularisation <= 0:
            message = (
                "inverse_regularisation must be a positive number,"
                f" not {self.inverse_regularisation!r}"
            )
            raise VerdictConfigError(message)
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or not 0 <= self.seed <= _MAX_SEED
        ):
            message = f"seed must be a whole number 0 .. {_MAX_SEED}, not {self.seed!r}"
            raise VerdictConfigError(message)
        if not _finite(self.partial_below) or not 0 < self.partial_below < 1:
            message = (
                "partial_below must be a probability strictly between 0 and 1,"
                f" not {self.partial_below!r}"
            )
            raise VerdictConfigError(message)
        if not isinstance(self.rubric, str) or not _RUBRIC.match(self.rubric):
            message = f"rubric must be a short lowercase name, not {self.rubric!r}"
            raise VerdictConfigError(message)

    def parameters(self) -> dict[str, object]:
        """The values, for a verdict model's manifest and its hash."""
        return {
            "train_until": self.train_until,
            "validate_until": self.validate_until,
            "inverse_regularisation": float(self.inverse_regularisation),
            "seed": self.seed,
            "partial_below": float(self.partial_below),
            "rubric": self.rubric,
        }


def parse_verdict_config(text: str) -> VerdictConfig:
    """Read a verdict configuration from TOML text.

    Raises:
        VerdictConfigError: Not TOML, an unknown key, or a value that cannot be
            obeyed.
    """
    try:
        stored = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"the verdict configuration is not TOML: {exc}"
        raise VerdictConfigError(message) from exc
    unknown = sorted(set(stored) - set(_KEYS))
    if unknown:
        message = f"unknown verdict settings: {', '.join(unknown)}"
        raise VerdictConfigError(message)
    return VerdictConfig(**stored)


def load_verdict_config(path: Path) -> VerdictConfig:
    """Read and resolve a verdict configuration file.

    Raises:
        VerdictConfigError: The file cannot be read or obeyed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        message = f"cannot read {path}: {exc}"
        raise VerdictConfigError(message) from exc
    return parse_verdict_config(text)


def verdict_config_sha256(config: VerdictConfig) -> bytes:
    """The hash of the resolved values, not of the file's bytes."""
    return hashlib.sha256(canonical_bytes(config.parameters())).digest()


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
    )

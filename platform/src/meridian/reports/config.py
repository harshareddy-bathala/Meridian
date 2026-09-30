"""An evaluation report's configuration: one strict file, one table per section.

A report is regenerable from a snapshot, this file and a seed (rule 8), so
everything a section needs is here, in a table named for it, and each table is
validated by the module that owns those settings rather than restated:

* ``[labels]`` — the labelling configuration, as ``meridian snapshot label``
  reads it (:func:`~meridian.datasets.label_config.label_config_from_mapping`);
* ``[prediction]`` — the model settings every configuration is fitted under,
  checked by :class:`~meridian.prediction.model_config.ModelConfig`, and the
  section's own: bootstrap resamples, and what counts as a disturbed pass and
  how many are needed before the Kp feature is judged at all (D-237).

Every table is optional and falls back to its owner's defaults. An unknown
table is refused, so a misspelt one cannot silently fall back.

**The seed is not in this file**, and neither is a model's configuration or
the groups it leaves out: the report fits every configuration, and derives
every seed. It is given with ``--seed``, and every
component's seed is derived from it (D-236). A ``seed`` here is refused rather
than ignored, because a reader who finds one would assume it was used.

The file is copied into the run exactly as given, so ``meridian report
verify`` rebuilds from the same bytes. Its hash is over the resolved values,
as every other configuration's is, so the hash in the header names what was
used, not how it was written.

Reference: docs/DECISIONS.md D-236.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.config_checks import LabelConfigError
from meridian.datasets.label_config import LabelConfig, label_config_from_mapping
from meridian.prediction.model_config import ModelConfig, ModelConfigError

__all__ = [
    "ConfigFile",
    "PredictionConfig",
    "ReportConfig",
    "ReportConfigError",
    "load_report_config",
    "parse_report_config",
    "report_config_sha256",
]

_TABLES = ("labels", "prediction")

_DECIDED_BY_THE_REPORT = ("configuration", "seed", "without")
"""Model settings the report sets itself, for every configuration it fits."""

_RESAMPLES = (100, 100_000)
_MAX_KP = 9.0


class ReportConfigError(ValueError):
    """A report configuration that cannot be read, or says something refused."""


@dataclass(frozen=True, slots=True)
class PredictionConfig:
    """How the prediction section fits and judges its models."""

    model: ModelConfig = field(default_factory=lambda: ModelConfig(configuration="A"))
    """The shared settings, held as configuration A's, since A is the one the
    archive population can take; each fit replaces the configuration, the seed
    and the groups left out."""

    resamples: int = 2000
    disturbed_kp: float = 5.0
    """Kp at or above which a pass counts as disturbed: NOAA's G1 storm."""

    min_disturbed: int = 30
    """Disturbed test passes needed before the Kp feature is judged; below it,
    it is reported untested (``EVALUATION.md`` §3), stated here in advance."""

    def parameters(self) -> dict[str, object]:
        """The values, without the settings each fit decides."""
        shared = {
            name: value
            for name, value in self.model.parameters().items()
            if name not in _DECIDED_BY_THE_REPORT
        }
        return {
            "model": shared,
            "resamples": self.resamples,
            "disturbed_kp": self.disturbed_kp,
            "min_disturbed": self.min_disturbed,
        }


@dataclass(frozen=True, slots=True)
class ReportConfig:
    """The resolved configuration of every section."""

    labels: LabelConfig = field(default_factory=LabelConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)

    def parameters(self) -> dict[str, object]:
        """The values, for the run's manifest and its header."""
        return {
            "labels": self.labels.parameters(),
            "prediction": self.prediction.parameters(),
        }


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """A configuration and the exact bytes it was read from."""

    config: ReportConfig
    text: bytes


def parse_report_config(text: bytes) -> ConfigFile:
    """Read a report configuration from the file's bytes.

    Args:
        text: The file's contents, UTF-8.

    Returns:
        The resolved configuration, beside the bytes it came from.

    Raises:
        ReportConfigError: Not UTF-8 TOML, an unknown table, a seed, or a
            table its owner refuses.
    """
    try:
        stored = tomllib.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        message = f"the report configuration is not UTF-8 TOML: {exc}"
        raise ReportConfigError(message) from exc
    if "seed" in stored:
        message = (
            "the seed is given with --seed, not in the configuration; every"
            " component's seed is derived from it (D-236)"
        )
        raise ReportConfigError(message)
    unknown = sorted(set(stored) - set(_TABLES))
    if unknown:
        message = f"unknown tables {unknown}; known: {sorted(_TABLES)}"
        raise ReportConfigError(message)
    try:
        labels = label_config_from_mapping(stored.get("labels", {}))
    except LabelConfigError as exc:
        message = f"[labels]: {exc}"
        raise ReportConfigError(message) from exc
    config = ReportConfig(
        labels=labels, prediction=_prediction(stored.get("prediction", {}))
    )
    return ConfigFile(config=config, text=text)


def _prediction(table: object) -> PredictionConfig:
    """``[prediction]``: model settings for ``ModelConfig``, and the section's own."""
    if not isinstance(table, dict):
        message = f"[prediction] must be a table, not {table!r}"
        raise ReportConfigError(message)
    decided = sorted(set(table) & set(_DECIDED_BY_THE_REPORT))
    if decided:
        message = (
            f"[prediction] sets {decided}; the report fits every configuration,"
            " leaves out each group it compares, and derives every seed (D-236)"
        )
        raise ReportConfigError(message)
    own = {
        name: table[name]
        for name in ("resamples", "disturbed_kp", "min_disturbed")
        if name in table
    }
    shared = {name: value for name, value in table.items() if name not in own}
    unknown = sorted(set(shared) - set(ModelConfig.__dataclass_fields__))
    if unknown:
        message = f"[prediction]: unknown settings {unknown}"
        raise ReportConfigError(message)
    try:
        model = ModelConfig(configuration="A", **shared)
    except ModelConfigError as exc:
        message = f"[prediction]: {exc}"
        raise ReportConfigError(message) from exc
    config = PredictionConfig(model=model, **own)
    _check_prediction(config)
    return config


def _check_prediction(config: PredictionConfig) -> None:
    """The section's own settings, each a number inside its range."""
    low, high = _RESAMPLES
    checks = (
        ("resamples", config.resamples, int, low, high),
        ("min_disturbed", config.min_disturbed, int, 1, 100_000),
        ("disturbed_kp", config.disturbed_kp, int | float, 0.0, _MAX_KP),
    )
    for name, value, kind, least, most in checks:
        if isinstance(value, bool) or not isinstance(value, kind):
            message = f"[prediction] {name} must be a number, not {value!r}"
            raise ReportConfigError(message)
        if not least <= value <= most:
            message = f"[prediction] {name} = {value} is outside {least}..{most}"
            raise ReportConfigError(message)


def load_report_config(path: Path) -> ConfigFile:
    """Read a report configuration file.

    Raises:
        ReportConfigError: The file cannot be read, or is refused.
    """
    try:
        text = path.read_bytes()
    except OSError as exc:
        message = f"cannot read the report configuration {path}: {exc.strerror}"
        raise ReportConfigError(message) from exc
    return parse_report_config(text)


def report_config_sha256(config: ReportConfig) -> bytes:
    """The configuration's hash, over its resolved values."""
    return hashlib.sha256(canonical_bytes(config.parameters())).digest()

"""An evaluation report's configuration: one strict file, one table per section.

A report is regenerable from a snapshot, this file and a seed (rule 8), so
everything a section needs is here, in a table named for it, and each table is
validated by the module that owns those settings rather than restated:

* ``[labels]`` — the labelling configuration, as ``meridian snapshot label``
  reads it (:func:`~meridian.datasets.label_config.label_config_from_mapping`).

Every table is optional and falls back to its owner's defaults. An unknown
table is refused, so a misspelt one cannot silently fall back.

**The seed is not in this file.** It is given with ``--seed``, and every
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

__all__ = [
    "ConfigFile",
    "ReportConfig",
    "ReportConfigError",
    "load_report_config",
    "parse_report_config",
    "report_config_sha256",
]

_TABLES = ("labels",)


class ReportConfigError(ValueError):
    """A report configuration that cannot be read, or says something refused."""


@dataclass(frozen=True, slots=True)
class ReportConfig:
    """The resolved configuration of every section."""

    labels: LabelConfig = field(default_factory=LabelConfig)

    def parameters(self) -> dict[str, object]:
        """The values, for the run's manifest and its header."""
        return {"labels": self.labels.parameters()}


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
    return ConfigFile(config=ReportConfig(labels=labels), text=text)


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

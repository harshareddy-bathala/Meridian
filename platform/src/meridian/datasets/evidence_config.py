"""What an evidence package holds: which populations, and how precisely.

One table, ``[export]``, with two keys:

* ``populations`` — ``["measured"]`` by default. Simulated receptions are in a
  package only when this names ``"simulated"``, and then in a file of their
  own (D-104): a researcher training on the package cannot mix the two by
  accident, and cannot get simulated rows without having asked for them.
* ``precision`` — ``"full"`` by default, every value as the snapshot holds it.
  ``"public"`` is the cut the public API may serve: every time to the minute,
  every angle to the degree, and nothing that times a station's sky more
  finely (D-093, D-369).

**A full-precision configuration hashes as it did before ``precision``
existed.** The key enters the hashed values only when it is ``"public"``, so
every package exported before it still regenerates to its own hash.

**Strict**, as the labelling configuration is: an unknown table or key, or a
population that is neither, is refused rather than ignored.

**The hash is of the values, not the file** (D-144): a comment or a reordered
key changes no package. The file's bytes are kept in the package as well, as
``config.toml``, so ``meridian dataset verify`` rebuilds from exactly what was
used.

Reference: docs/DECISIONS.md D-104, D-144, D-311.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "POPULATIONS",
    "PRECISIONS",
    "EvidenceConfig",
    "EvidenceConfigError",
    "EvidenceConfigFile",
    "Precision",
    "evidence_config_sha256",
    "load_evidence_config",
    "parse_evidence_config",
]

POPULATIONS = ("measured", "simulated")
"""Every population, in the order a package lists them."""

Precision = Literal["full", "public"]
PRECISIONS: tuple[Precision, ...] = ("full", "public")
"""``full`` keeps every value; ``public`` is the cut the public API serves."""

_KEYS = frozenset({"populations", "precision"})


class EvidenceConfigError(ValueError):
    """The evidence configuration cannot be read, or holds a bad value."""


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """The resolved evidence configuration."""

    populations: tuple[str, ...] = ("measured",)
    precision: Precision = "full"

    def parameters(self) -> dict[str, object]:
        """The resolved values, as the hash and the manifest record them.

        ``precision`` only when it is ``public``, so a full-precision package
        hashes as every package did before the key existed.
        """
        values: dict[str, object] = {"populations": list(self.populations)}
        if self.precision == "public":
            values["precision"] = self.precision
        return values


@dataclass(frozen=True, slots=True)
class EvidenceConfigFile:
    """A configuration and the exact bytes it was read from."""

    config: EvidenceConfig
    text: bytes


def evidence_config_sha256(config: EvidenceConfig) -> bytes:
    """The sha256 of the resolved values, rendered canonically."""
    return hashlib.sha256(canonical_bytes(config.parameters())).digest()


def parse_evidence_config(text: bytes) -> EvidenceConfigFile:
    """Read a configuration from its bytes.

    Raises:
        EvidenceConfigError: It is not TOML, or holds a key or value refused.
    """
    try:
        parsed = tomllib.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EvidenceConfigError(f"the configuration is not TOML: {exc}") from exc
    unknown = set(parsed) - {"export"}
    if unknown:
        raise EvidenceConfigError(f"unknown tables {sorted(unknown)}")
    table = parsed.get("export", {})
    if not isinstance(table, dict):
        raise EvidenceConfigError("[export] must be a table")
    if set(table) - _KEYS:
        raise EvidenceConfigError(f"[export] unknown keys {sorted(set(table) - _KEYS)}")
    return EvidenceConfigFile(
        config=EvidenceConfig(
            populations=_populations(table.get("populations")),
            precision=_precision(table.get("precision", "full")),
        ),
        text=text,
    )


def load_evidence_config(path: Path) -> EvidenceConfigFile:
    """Read the configuration file at ``path``.

    Raises:
        EvidenceConfigError: It cannot be read, or is refused.
    """
    try:
        text = path.read_bytes()
    except OSError as exc:
        raise EvidenceConfigError(f"{path} cannot be read: {exc.strerror}") from exc
    return parse_evidence_config(text)


def _populations(raw: object) -> tuple[str, ...]:
    if raw is None:
        return EvidenceConfig().populations
    if not isinstance(raw, list) or not raw:
        raise EvidenceConfigError("[export] populations must be a non-empty list")
    if any(one not in POPULATIONS for one in raw):
        raise EvidenceConfigError(
            f"a population is one of {', '.join(POPULATIONS)}, not {raw!r}"
        )
    if len(set(raw)) != len(raw):
        raise EvidenceConfigError("[export] populations names one twice")
    return tuple(one for one in POPULATIONS if one in raw)


def _precision(raw: object) -> Precision:
    for one in PRECISIONS:
        if raw == one:
            return one
    raise EvidenceConfigError(
        f"[export] precision is one of {', '.join(PRECISIONS)}, not {raw!r}"
    )

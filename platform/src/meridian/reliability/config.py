"""What the reliability layer classifies under, and the targets it reports against.

Three tables, read from ``deploy/reliability.toml`` or taken as defaults:

* ``[classification]`` — the margins a pass is classified under. Every stored
  classification records the sha256 of these, so a figure names what it was
  counted under, and changing one writes new rows beside the old (D-182). The
  defaults are the snapshot labeller's (D-146, D-147).
* ``[slo]`` — the window every figure is counted over, and the target each is
  judged against (D-184). Changing a target changes no classification, so it
  is not in the hash.
* ``[diagnosis]`` — the thresholds a loss's cause is judged by (D-273). Every
  stored diagnosis records the sha256 of these, as a classification does.

**Strict**, as the labelling configuration is: an unknown key, a wrong type or
a value out of range is refused by name, because a misspelled target that
silently kept its default is a target somebody believes they set.

Standard library only: the snapshot command reads it beside the labeller.

Reference: docs/DECISIONS.md D-146, D-147, D-182, D-184, D-273 to D-277.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TypeVar

__all__ = [
    "RELIABILITY_CONFIG_ENV",
    "ClassificationConfig",
    "DiagnosisConfig",
    "ReliabilityConfig",
    "ReliabilityConfigError",
    "SloConfig",
    "load_deployed_reliability_config",
    "load_reliability_config",
    "parse_reliability_config",
]

_DAY_S = 86_400

RELIABILITY_CONFIG_ENV = "MERIDIAN_RELIABILITY_CONFIG"
"""The deployment's reliability file, read by the jobs service, the API and
its metrics. Unset means the defaults, which ``deploy/reliability.toml.example``
spells out. Every process of one deployment must read the same file: the
classification's hash is how the API finds the rows the jobs service wrote."""


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


def _number(name: str, value: object, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReliabilityConfigError(f"{name} must be a number, not {value!r}")
    if not low <= value <= high:
        raise ReliabilityConfigError(f"{name} must be in {low}..{high}, not {value}")


def _canonical_sha256(parameters: dict[str, object]) -> bytes:
    text = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).digest()


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
        return _canonical_sha256(dict(self.parameters()))


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
class DiagnosisConfig:
    """The thresholds a loss's cause is judged by (D-273 to D-277).

    Each was chosen with the simulator's fault effects in view, which D-270
    records; changing one writes every diagnosis again beside the old rows.
    """

    heard_snr_db: float = 3.0
    """A sample at or above this heard the satellite: the detection bar."""

    lookback_s: int = 14 * _DAY_S
    """How far back the station's own history is read, for its loss map and
    its noise baseline."""

    obstruction_sector_deg: float = 10.0
    """The width of the loss map's sectors."""

    obstruction_ceiling_deg: float = 45.0
    """No loss above this elevation is read as an obstruction."""

    elevation_match_deg: float = 5.0
    """How close in elevation a heard sample on the pass's other side must be
    for a lost one to count as lost where it would have been heard."""

    obstruction_min_passes: int = 2
    """Distinct earlier passes a sector's losses must come from to mark it."""

    obstruction_min_lost_share: float = 0.6
    """The share of a sector's comparable samples that must be lost to mark it."""

    obstruction_min_samples: int = 2
    """Lost samples of a heard reception inside marked sectors to name it."""

    obstruction_absent_share: float = 0.8
    """The share of a silent reception's audible samples that must lie inside
    marked sectors to name it."""

    audible_min_elevation_deg: float = 10.0
    """Samples below this say little about whether anything blocked them."""

    interference_lift_db: float = 2.0
    """A floor this far above the station's own at the same gain is raised."""

    interference_min_baseline: int = 5
    """The station's own readings needed before its floor is a baseline."""

    silent_window_s: int = 2_700
    """How far either side of the pass another station's attempt counts as
    evidence about the satellite: contemporaneous, unlike D-147's (D-276)."""

    silent_min_attempts: int = 1
    """Other stations that must have listened and heard nothing, with none
    hearing it, to name the satellite (D-276)."""

    silent_min_elevation_deg: float = 40.0
    """A silence counts, the loss's own included, only from a pass that climbed
    this high: a low pass hearing nothing is the usual case, a high one is not
    (D-276)."""

    timing_tolerance_s: float = 30.0
    """Added to the assignment's stated timing uncertainty before a clock or a
    window is called wrong: a heartbeat's own cadence and transit."""

    clock_margin_s: int = 60
    """How far either side of the window a heartbeat's clock is read: one
    heartbeat's cadence and a margin, so the clock read is the one the pass
    was received under, not the next hour's (D-277)."""

    conflict_margin: float = 0.2
    """The lead the best-supported cause needs over the next to be named."""

    def __post_init__(self) -> None:
        """Refuse a threshold outside the range the tests were written for."""
        _number("heard_snr_db", self.heard_snr_db, 0.0, 30.0)
        _whole("lookback_s", self.lookback_s, 0, 90 * _DAY_S)
        _number("obstruction_sector_deg", self.obstruction_sector_deg, 1.0, 90.0)
        _number("obstruction_ceiling_deg", self.obstruction_ceiling_deg, 0.0, 90.0)
        _number("elevation_match_deg", self.elevation_match_deg, 0.1, 30.0)
        _whole("obstruction_min_passes", self.obstruction_min_passes, 1, 100)
        _share("obstruction_min_lost_share", self.obstruction_min_lost_share)
        _whole("obstruction_min_samples", self.obstruction_min_samples, 1, 100)
        _share("obstruction_absent_share", self.obstruction_absent_share)
        _number("audible_min_elevation_deg", self.audible_min_elevation_deg, 0.0, 90.0)
        _number("interference_lift_db", self.interference_lift_db, 0.1, 40.0)
        _whole("interference_min_baseline", self.interference_min_baseline, 1, 1000)
        _whole("silent_window_s", self.silent_window_s, 1, 7 * _DAY_S)
        _whole("silent_min_attempts", self.silent_min_attempts, 1, 100)
        _number("silent_min_elevation_deg", self.silent_min_elevation_deg, 0.0, 90.0)
        _number("timing_tolerance_s", self.timing_tolerance_s, 0.0, 3_600.0)
        _whole("clock_margin_s", self.clock_margin_s, 0, _DAY_S)
        _share("conflict_margin", self.conflict_margin)

    def parameters(self) -> dict[str, float]:
        """The thresholds as recorded with every diagnosis.

        A real threshold is written as a real however the file spelled it, so
        ``interference_lift_db = 2`` and ``2.0`` are one configuration with one
        hash, and not every loss diagnosed again for a missing ``.0``.
        """
        held = asdict(self)
        return {
            one.name: float(held[one.name]) if one.type == "float" else held[one.name]
            for one in fields(self)
        }

    def sha256(self) -> bytes:
        """The hash stored beside every diagnosis made under these."""
        return _canonical_sha256(dict(self.parameters()))


@dataclass(frozen=True, slots=True)
class ReliabilityConfig:
    """Every table, resolved."""

    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    slo: SloConfig = field(default_factory=SloConfig)
    diagnosis: DiagnosisConfig = field(default_factory=DiagnosisConfig)


_TABLES = ("classification", "slo", "diagnosis")

_T = TypeVar("_T", ClassificationConfig, SloConfig, DiagnosisConfig)


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
        diagnosis=_table(stored, "diagnosis", DiagnosisConfig),
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


def load_deployed_reliability_config() -> ReliabilityConfig:
    """The file :data:`RELIABILITY_CONFIG_ENV` names, or the defaults.

    Raises:
        ReliabilityConfigError: The file named cannot be read, or is refused.
    """
    named = os.environ.get(RELIABILITY_CONFIG_ENV, "").strip()
    return load_reliability_config(Path(named) if named else None)

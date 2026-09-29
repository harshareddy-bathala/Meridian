"""The regional report's configuration: periods, thresholds, seed — one strict file.

A regional report is a pure function of a raw snapshot, this configuration and
the seed in it (rule 8). Every value is here rather than in code, so the
threshold that raised an alert is readable beside the alert, and the hash in
the report's manifest names exactly the values it was computed under.

* ``[baseline]`` and ``[current]`` — ``from`` and ``until`` (half-open) of the
  two periods a change is measured between. Both or neither: with neither, a
  report has series and coverage and no change or alert.
* ``[rules.<quantity>]`` — ``kind`` (``relative`` or ``absolute``) and a signed
  ``threshold``: negative watches for a fall, positive for a rise. An alert is
  raised only when the whole confidence interval of the change lies beyond it
  (D-231).
* ``seed``, ``resamples`` and ``confidence`` — the bootstrap that gives each
  change its interval.
* ``min_points`` — fewer values than this in either period is "insufficient",
  never a change and never an alert.
* ``nearest_km`` — how far from an area a point product's cell may be and still
  describe it, when no cell lies inside.
* ``swath_km`` — the width of ground a reception images, for coverage (D-230).
* ``wet_day_mm`` — the day's precipitation above which a day is wet, for the
  receiving-chain cross-check (D-233).
* ``include_simulated`` — false unless simulated receptions are asked for by
  name; when true they are counted apart and labelled (rule 5).

**Strict:** an unknown key, a wrong type or a value off its scale is refused
by name. Reference: docs/DECISIONS.md D-229 to D-233.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "DEFAULT_RULES",
    "Period",
    "RegionsConfig",
    "RegionsConfigError",
    "Rule",
    "load_regions_config",
    "parse_regions_config",
]

_TOP = frozenset(
    {
        "seed",
        "resamples",
        "confidence",
        "min_points",
        "nearest_km",
        "swath_km",
        "wet_day_mm",
        "include_simulated",
        "baseline",
        "current",
        "rules",
    }
)
_KINDS = ("relative", "absolute")


class RegionsConfigError(ValueError):
    """A regional configuration that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class Period:
    """``[start, until)``, both UTC."""

    start: datetime
    until: datetime

    def __post_init__(self) -> None:
        """Refuse a period that is naive or empty."""
        if self.start.tzinfo is None or self.until.tzinfo is None:
            message = "a period's ends carry an offset; write them with Z"
            raise RegionsConfigError(message)
        if self.until <= self.start:
            message = f"a period ending {self.until} does not follow {self.start}"
            raise RegionsConfigError(message)

    def holds(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside it."""
        return self.start <= moment < self.until


@dataclass(frozen=True, slots=True)
class Rule:
    """What counts as a change worth an alert for one quantity."""

    kind: str
    threshold: float

    def __post_init__(self) -> None:
        """Refuse an unknown kind, or a threshold that watches for nothing."""
        if self.kind not in _KINDS:
            message = f"rule kind {self.kind!r} is not one of {', '.join(_KINDS)}"
            raise RegionsConfigError(message)
        if self.threshold == 0:
            message = "a threshold of 0 watches for nothing; give it a sign and a size"
            raise RegionsConfigError(message)


DEFAULT_RULES: Mapping[str, Rule] = {
    "ndvi": Rule("relative", -0.15),
    "fire_count": Rule("absolute", 3.0),
    "precipitation": Rule("relative", -0.5),
    "aerosol_optical_depth": Rule("relative", 0.5),
    "night_lights_radiance": Rule("relative", -0.3),
}
"""A fall in vegetation of 15%, three more fire detections a day, half the
rain, half as much aerosol again, a third of the night-time light gone. Stated,
not tuned: they are the operator's to change, in the file, per area set."""


@dataclass(frozen=True, slots=True)
class RegionsConfig:
    """The resolved configuration."""

    baseline: Period | None = None
    current: Period | None = None
    rules: Mapping[str, Rule] = field(default_factory=lambda: dict(DEFAULT_RULES))
    seed: int = 0
    resamples: int = 2000
    confidence: float = 0.95
    min_points: int = 3
    nearest_km: float = 50.0
    swath_km: float = 2800.0
    """Meteor-M's MSU-MR images about 2 800 km across track."""

    wet_day_mm: float = 1.0
    include_simulated: bool = False

    def __post_init__(self) -> None:
        """Refuse values off their scale, and one period without the other."""
        if (self.baseline is None) != (self.current is None):
            message = "give both [baseline] and [current], or neither"
            raise RegionsConfigError(message)
        _within("resamples", self.resamples, 100, 100_000)
        _within("confidence", self.confidence, 0.5, 0.999)
        _within("min_points", self.min_points, 2, 10_000)
        _within("nearest_km", self.nearest_km, 0.0, 1_000.0)
        _within("swath_km", self.swath_km, 1.0, 10_000.0)
        _within("wet_day_mm", self.wet_day_mm, 0.0, 1_000.0)
        _within("seed", self.seed, 0, 2**32 - 1)
        flag: object = self.include_simulated
        if not isinstance(flag, bool):
            message = f"include_simulated is true or false, not {flag!r}"
            raise RegionsConfigError(message)

    def parameters(self) -> dict[str, object]:
        """Every resolved value, JSON-native, for the report's manifest."""
        return {
            "baseline": _period(self.baseline),
            "current": _period(self.current),
            "rules": {
                name: {"kind": rule.kind, "threshold": rule.threshold}
                for name, rule in sorted(self.rules.items())
            },
            "seed": self.seed,
            "resamples": self.resamples,
            "confidence": self.confidence,
            "min_points": self.min_points,
            "nearest_km": self.nearest_km,
            "swath_km": self.swath_km,
            "wet_day_mm": self.wet_day_mm,
            "include_simulated": self.include_simulated,
        }

    @property
    def sha256(self) -> bytes:
        """The hash of the resolved values, which the report's manifest names."""
        return hashlib.sha256(canonical_bytes(self.parameters())).digest()


def load_regions_config(path: Path) -> RegionsConfig:
    """Read a configuration file.

    Raises:
        RegionsConfigError: It is not readable TOML, or not obeyable.
    """
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        message = f"{path} could not be read as TOML: {exc}"
        raise RegionsConfigError(message) from exc
    return parse_regions_config(table)


def parse_regions_config(table: Mapping[str, object]) -> RegionsConfig:
    """The configuration from a parsed TOML table.

    Raises:
        RegionsConfigError: An unknown key, a wrong type, or a value off scale.
    """
    unknown = sorted(set(table) - _TOP)
    if unknown:
        message = f"unknown keys {unknown}; known: {', '.join(sorted(_TOP))}"
        raise RegionsConfigError(message)
    rules = dict(DEFAULT_RULES) | _rules(table.get("rules", {}))
    scalars = {
        name: table[name]
        for name in _TOP - {"baseline", "current", "rules"}
        if name in table
    }
    try:
        return RegionsConfig(
            baseline=_period_from(table.get("baseline"), "baseline"),
            current=_period_from(table.get("current"), "current"),
            rules=rules,
            **scalars,  # type: ignore[arg-type]
        )
    except TypeError as exc:
        raise RegionsConfigError(str(exc)) from exc


def _rules(value: object) -> dict[str, Rule]:
    if not isinstance(value, dict):
        message = "[rules] is a table of [rules.<quantity>] tables"
        raise RegionsConfigError(message)
    rules = {}
    for name, entry in value.items():
        if not isinstance(entry, dict) or set(entry) != {"kind", "threshold"}:
            message = f"[rules.{name}] has exactly kind and threshold"
            raise RegionsConfigError(message)
        threshold = entry["threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            message = f"rules.{name}.threshold is a number"
            raise RegionsConfigError(message)
        rules[str(name)] = Rule(str(entry["kind"]), float(threshold))
    return rules


def _period_from(value: object, name: str) -> Period | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"from", "until"}:
        message = f"[{name}] has exactly from and until"
        raise RegionsConfigError(message)
    return Period(_moment(value["from"], name), _moment(value["until"], name))


def _moment(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            message = f"[{name}] {value!r} is not an ISO 8601 instant"
            raise RegionsConfigError(message) from exc
    message = f"[{name}] ends are instants, not {value!r}"
    raise RegionsConfigError(message)


def _period(period: Period | None) -> dict[str, datetime] | None:
    return None if period is None else {"from": period.start, "until": period.until}


def _within(name: str, value: object, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} is a number, not {value!r}"
        raise RegionsConfigError(message)
    if not low <= value <= high:
        message = f"{name} is {value}, outside {low} to {high}"
        raise RegionsConfigError(message)

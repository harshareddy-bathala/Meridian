"""The service level indicators, as pure functions of classified passes.

Each takes the passes one population classified inside a window and returns a
count over a count, never a bare percentage, so every figure can be checked by
counting rows (the roadmap's Stage 20 gate). The live report hands these
functions rows from ``pass_classifications``; the snapshot command hands them
rows from ``labels.jsonl``. The definitions are D-184's:

* **pass capture rate** (SC-4) — captured over passes the station could have
  captured: every classified pass except the two whose outcome is about the
  satellite, not the station;
* **confirmed miss rate** — misses over those same passes on which listening
  was confirmed, since only a confirmed-listening pass can be a miss;
* **assignment completion rate** — passes the station reported on, whatever
  the outcome, over every classified pass;
* **schedule execution rate** — passes the station attempted (any report but
  ``not_attempted``) over every classified pass;
* **observation submission delay** — p50 and p95 of each report's first
  arrival after its window closed.

Station availability is a share of seconds, not of passes, and is read from
heartbeats where they are held whole (:class:`Share`).

Standard library only, as the classification is (D-180).

Reference: docs/DECISIONS.md D-180, D-184; docs/EVALUATION.md §1.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import ceil, sqrt

from meridian.reliability.classification import CAPTURED, MISS, SATELLITE_CLASSES

__all__ = [
    "Delays",
    "PassRecord",
    "Proportion",
    "Share",
    "assignment_completion_rate",
    "capture_rate",
    "confirmed_miss_rate",
    "delays",
    "eligible",
    "schedule_execution_rate",
]

_Z_95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class PassRecord:
    """One classified pass, as every indicator reads it."""

    reference: str
    """Where the row came from: the representative assignment id (live) or
    ``pass:<id>`` (a snapshot), so a debit can be looked up."""

    station_id: str
    window_end: datetime
    classification: str
    listening_confirmed: bool
    outcome: str | None
    """The report's outcome, or None if no report arrived."""

    simulated: bool


@dataclass(frozen=True, slots=True)
class Proportion:
    """A count over a count, with its Wilson 95% interval.

    The interval is the one ``meridian.datasets.weighting.wilson`` computes. It
    is restated here because this module may not import a dataset module
    (``tests/unit/test_reliability_boundaries.py``).
    """

    numerator: int
    denominator: int

    @property
    def estimate(self) -> float | None:
        """The share, or None where nothing was counted."""
        return self.numerator / self.denominator if self.denominator else None

    @property
    def interval(self) -> tuple[float, float] | None:
        """The Wilson 95% interval, which stays inside 0..1 at small n."""
        if not self.denominator:
            return None
        p, n = self.numerator / self.denominator, self.denominator
        z2 = _Z_95 * _Z_95
        scale = 1 + z2 / n
        centre = (p + z2 / (2 * n)) / scale
        half = _Z_95 * sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / scale
        return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True, slots=True)
class Share:
    """Seconds covered over seconds measured."""

    covered_s: float
    span_s: float

    @property
    def estimate(self) -> float | None:
        """The share, or None where nothing was measured."""
        return self.covered_s / self.span_s if self.span_s > 0 else None

    def __add__(self, other: Share) -> Share:
        """Pool two spans, as a population pools its stations."""
        return Share(self.covered_s + other.covered_s, self.span_s + other.span_s)


@dataclass(frozen=True, slots=True)
class Delays:
    """Nearest-rank percentiles of a set of delays, in seconds."""

    n: int
    p50_s: float | None
    p95_s: float | None


def eligible(passes: Iterable[PassRecord]) -> list[PassRecord]:
    """Passes the station could have captured: not judged by the satellite."""
    return [one for one in passes if one.classification not in SATELLITE_CLASSES]


def capture_rate(passes: Iterable[PassRecord]) -> Proportion:
    """SC-4: captured passes over passes the station could have captured."""
    held = eligible(passes)
    return Proportion(
        sum(1 for one in held if one.classification in CAPTURED), len(held)
    )


def confirmed_miss_rate(passes: Iterable[PassRecord]) -> Proportion:
    """Misses over the eligible passes on which listening was confirmed."""
    held = [one for one in eligible(passes) if one.listening_confirmed]
    return Proportion(sum(1 for one in held if one.classification == MISS), len(held))


def assignment_completion_rate(passes: Iterable[PassRecord]) -> Proportion:
    """Passes the station reported on, whatever it heard, over every pass."""
    held = list(passes)
    return Proportion(sum(1 for one in held if one.outcome is not None), len(held))


def schedule_execution_rate(passes: Iterable[PassRecord]) -> Proportion:
    """Passes the station attempted over every pass.

    ``not_attempted`` is a report that the station did not start (D-121), so
    it completes the assignment without executing it.
    """
    held = list(passes)
    attempted = sum(1 for one in held if one.outcome not in (None, "not_attempted"))
    return Proportion(attempted, len(held))


def delays(seconds: Sequence[float]) -> Delays:
    """The median and 95th percentile by nearest rank, of however many there are."""
    ordered = sorted(seconds)
    if not ordered:
        return Delays(0, None, None)
    return Delays(len(ordered), _rank(ordered, 0.50), _rank(ordered, 0.95))


def _rank(ordered: Sequence[float], q: float) -> float:
    return ordered[max(0, ceil(q * len(ordered)) - 1)]

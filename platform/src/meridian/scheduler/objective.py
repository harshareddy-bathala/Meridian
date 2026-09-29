"""What a pass is worth to the schedule, term by term — D-168.

The optimiser maximises the summed value of what it selects. A candidate's
value is a product of three terms, each stored on its decision so a reader can
see why one pass was worth more than another:

* **yield** — the probability the pass decodes. A published model's, for the
  configuration (B uses A's model, D-160). With no model configured it is the
  **elevation proxy**, peak elevation over 90°, labelled as such, and allowed
  for A and B only: C and D are learned configurations, and a schedule labelled
  D that no model made would be a claim the data never backed;
* **frames** — how long the satellite is above the horizon, ``los − aos`` in
  seconds, since a decoded pass returns frames for as long as it is received.
  The pass, not the assignment window: the margin either side is recording
  time spent waiting for a pass whose timing is uncertain, not signal. A
  configuration can state ``none`` instead, and every pass counts as one;
* **priority** — the operator's weight for the satellite, under B and D only.
  A and C maximise expected yield; B and D maximise it weighted as an operator
  would, so D − B isolates the model and C − A isolates our features. This
  amends D-160, where D was unweighted.

Fairness and coverage terms are optional in the roadmap and not built: one
station's schedule has nobody to be fair to, and coverage is what the
priority term expresses when an operator wants it.

A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-066, D-160, D-168.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from meridian.scheduler import Candidate, ScoredCandidate

__all__ = [
    "ELEVATION_PROXY",
    "FRAMES",
    "MODEL",
    "PRIORITY_WEIGHTED",
    "Terms",
    "Yield",
    "elevation_proxy",
    "frames_of",
    "modelled",
    "value_candidates",
]

MODEL = "model"
ELEVATION_PROXY = "elevation_proxy"

FRAMES = ("duration", "none")
"""What the frames term counts: the pass's seconds above the horizon, or one."""

PRIORITY_WEIGHTED = frozenset({"B", "D"})
"""The configurations whose objective the operator's priority weights (D-168)."""

_LEARNED = frozenset({"C", "D"})
_ZENITH_DEG = 90.0


@dataclass(frozen=True, slots=True)
class Yield:
    """The probability a pass decodes, and where it came from."""

    probability: float
    source: str
    """``model`` or ``elevation_proxy``."""

    path: str | None
    """The scoring route (D-161) where a model gave it; ``None`` for the proxy."""

    reason: str

    def __post_init__(self) -> None:
        """Refuse a probability that is not one."""
        if not 0.0 <= self.probability <= 1.0:
            message = f"a yield of {self.probability} is not a probability"
            raise ValueError(message)
        if self.source not in (MODEL, ELEVATION_PROXY):
            message = f"yield source {self.source!r} is not {MODEL} or proxy"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Terms:
    """Everything a candidate's value is made of."""

    expected: Yield
    frames: float
    """Seconds above the horizon, or 1 under ``frames = "none"``."""

    frames_term: str
    priority: float
    priority_weighted: bool

    @property
    def value(self) -> float:
        """Yield × frames × priority, the last under B and D only."""
        weight = self.priority if self.priority_weighted else 1.0
        return self.expected.probability * self.frames * weight


def elevation_proxy(candidate: Candidate) -> Yield:
    """Peak elevation over 90°, standing in for a probability no model gave."""
    share = min(max(candidate.max_elevation_deg / _ZENITH_DEG, 0.0), 1.0)
    return Yield(
        probability=share,
        source=ELEVATION_PROXY,
        path=None,
        reason="no model configured: peak elevation over 90°",
    )


def modelled(probability: float, path: str, reason: str) -> Yield:
    """A model's probability for a pass, with its route (D-161)."""
    return Yield(probability=probability, source=MODEL, path=path, reason=reason)


def frames_of(candidate: Candidate, term: str) -> float:
    """The frames term: seconds from ``aos`` to ``los``, or one."""
    if term not in FRAMES:
        message = f"frames must be one of {list(FRAMES)}, not {term!r}"
        raise ValueError(message)
    if term == "none":
        return 1.0
    return (candidate.los - candidate.aos).total_seconds()


def value_candidates(
    candidates: Sequence[Candidate],
    *,
    configuration: str,
    frames_term: str,
    yields: Mapping[int, Yield] | None,
) -> tuple[list[ScoredCandidate], dict[int, Terms]]:
    """Each candidate's terms, and the candidate scored by their product.

    Args:
        candidates: The passes to value.
        configuration: ``A`` to ``D``; B and D weight by priority.
        frames_term: ``duration`` or ``none``.
        yields: Each candidate's probability by pass id, from the model; or
            ``None`` where no model is configured, when every candidate takes
            the elevation proxy.

    Returns:
        The scored candidates, in the order given, and their terms by pass id.

    Raises:
        ValueError: A learned configuration without a model's yields, a
            candidate the model gave no yield for, or a priority that is not a
            positive number — zero or less would make a pass worth nothing, or
            worth avoiding, without anybody saying so (D-066).
    """
    if yields is None and configuration in _LEARNED:
        message = (
            f"configuration {configuration} is a learned model and none is"
            " configured; the elevation proxy stands in for A and B only"
        )
        raise ValueError(message)
    scored, terms = [], {}
    for candidate in candidates:
        if not math.isfinite(candidate.priority) or candidate.priority <= 0:
            message = (
                f"pass {candidate.pass_id} has priority {candidate.priority};"
                " a priority is a positive number"
            )
            raise ValueError(message)
        if yields is None:
            expected = elevation_proxy(candidate)
        elif candidate.pass_id in yields:
            expected = yields[candidate.pass_id]
        else:
            message = f"the model gave pass {candidate.pass_id} no yield"
            raise ValueError(message)
        held = Terms(
            expected=expected,
            frames=frames_of(candidate, frames_term),
            frames_term=frames_term,
            priority=candidate.priority,
            priority_weighted=configuration in PRIORITY_WEIGHTED,
        )
        terms[candidate.pass_id] = held
        scored.append(ScoredCandidate(candidate=candidate, score=held.value))
    return scored, terms

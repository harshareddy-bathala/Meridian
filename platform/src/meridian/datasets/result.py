"""The one way to state a result from an evaluation dataset — D-154.

Stage 16's gate is that every archive-derived result carries its completeness
*automatically*. A report template someone remembers to fill in is not
automatic, so the gate is a type: an :class:`EvaluationResult` cannot be built
without a :class:`~meridian.datasets.completeness.CompletenessSummary`, and it
carries either the weighting's :class:`~meridian.datasets.weighting.IpwDiagnostics`
or a :class:`NotWeighted` that says why not. Stage 17's figures are built as
these, so a figure without its completeness is a type error rather than a
review comment.

Reference: docs/DECISIONS.md D-151, D-153, D-154.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.datasets.completeness import POPULATIONS, CompletenessSummary
from meridian.datasets.weighting import IpwDiagnostics

__all__ = [
    "EvaluationResult",
    "NotWeighted",
    "Weighting",
]


@dataclass(frozen=True, slots=True)
class NotWeighted:
    """A stated reason a result carries no weights — never an absent field."""

    reason: str

    def __post_init__(self) -> None:
        """Refuse an empty reason, which would be an absent field by another name."""
        if not isinstance(self.reason, str) or not self.reason.strip():
            message = "a result that is not weighted must say why"
            raise ValueError(message)

    def parameters(self) -> dict[str, object]:
        """The reason, as it is written."""
        return {"not_weighted": self.reason}


Weighting = IpwDiagnostics | NotWeighted


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """One population's result, with the completeness it must be read beside."""

    population: str
    completeness: CompletenessSummary
    weighting: Weighting

    def __post_init__(self) -> None:
        """Refuse a result without its completeness, or with a stranger's.

        The annotations already say this to a type checker; this says it to
        a notebook, where nothing checks them.
        """
        if self.population not in POPULATIONS:
            message = f"unknown population {self.population!r}"
            raise ValueError(message)
        # Held as plain objects, so the checks survive the annotations' narrowing.
        held: dict[str, object] = {
            "completeness": self.completeness,
            "weighting": self.weighting,
        }
        if not isinstance(held["completeness"], CompletenessSummary):
            message = "an evaluation result is built with its completeness (D-154)"
            raise TypeError(message)
        if self.completeness.population != self.population:
            message = (
                f"a result for {self.population} carries the completeness of "
                f"{self.completeness.population}"
            )
            raise ValueError(message)
        if not isinstance(held["weighting"], IpwDiagnostics | NotWeighted):
            message = "an evaluation result carries its weights, or says why not"
            raise TypeError(message)

    @property
    def unreliable(self) -> bool:
        """Whether its weighted estimate must be labelled rather than quoted."""
        return isinstance(self.weighting, IpwDiagnostics) and self.weighting.unreliable

    def parameters(self) -> dict[str, object]:
        """The result as plain values, for a manifest or a report."""
        return {
            "completeness": self.completeness.parameters(),
            "weighting": self.weighting.parameters(),
        }

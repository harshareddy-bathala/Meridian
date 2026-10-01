"""The verdict's examples: labelled, measured receptions, split by date.

An example is one observation revision whose label D-260's rules give (see
:mod:`meridian.datasets.usable_labels`). It is left out, and counted, when:
- it is simulated, because no simulated row is ever training or test input
  (D-078, D-105);
- it is unrated, because an unrated reception has no label;
- its rating was made under another rubric, because that is another label.

**Which examples each route learns from.** A route's model reads a subset of
the inputs, so it can learn from every example that has them: ``outcome`` from
all, ``snr`` from every example with an SNR, and ``full`` from those that also
have a frames ratio. Each example is *scored* by its own route only
(D-261).

**The split is temporal, by stated dates**, exactly as Stage 17's
(:func:`meridian.prediction.splits.temporal_split`, D-162). Training is before
``train_until``, validation before ``validate_until``, and test from there
to the snapshot's ``as_of``. A reception's instant is when it started.

Reference: docs/DECISIONS.md D-078, D-105, D-162, D-260, D-261.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.usable_labels import RATING, Revision, UsableLabel
from meridian.prediction.examples import Example
from meridian.prediction.splits import SplitError
from meridian.prediction.verdict_inputs import (
    ROUTES,
    feature_values,
    route_of,
)
from meridian.prediction.verdict_rows import Reception

__all__ = [
    "VerdictExample",
    "VerdictExamples",
    "VerdictSplit",
    "build_verdict_examples",
    "learns_from",
    "split_verdict_examples",
    "verdict_counts",
]

POPULATION = "verdict"


@dataclass(frozen=True, slots=True)
class VerdictExample:
    """One labelled reception."""

    reception: Reception
    usable: bool
    source: str
    """``rating``, or ``no_product`` for a reception with nothing to rate."""

    @property
    def route(self) -> str:
        """The route this reception is scored by."""
        return route_of(self.reception.inputs)

    @property
    def rated(self) -> bool:
        """Whether a person rated it, rather than its having no product."""
        return self.source == RATING

    @property
    def example(self) -> Example:
        """As the fitter's example: its features, its label, its instant."""
        return Example(
            population=POPULATION,
            station_id=self.reception.station_id,
            satellite_id=self.reception.satellite_id,
            aos=self.reception.started_at,
            positive=self.usable,
            features=feature_values(self.reception.inputs),
            station_history=0,
        )


@dataclass(frozen=True, slots=True)
class VerdictExamples:
    """Every usable example, and what was left out and why."""

    examples: tuple[VerdictExample, ...]
    simulated: int
    unrated: int
    other_rubric: int


@dataclass(frozen=True, slots=True)
class VerdictSplit:
    """The three spans, each oldest first, and the dates that made them."""

    train_until: datetime
    validate_until: datetime
    as_of: datetime
    train: tuple[VerdictExample, ...]
    validate: tuple[VerdictExample, ...]
    test: tuple[VerdictExample, ...]


def build_verdict_examples(
    receptions: Iterable[Reception],
    labels: Mapping[Revision, UsableLabel],
    *,
    rubric: str,
) -> VerdictExamples:
    """Join each reception to its label, leaving out what may not be learned from.

    Args:
        receptions: Every observation revision in a snapshot.
        labels: Each revision's label, from the same snapshot.
        rubric: The rating instructions a rated label must have been made under.

    Returns:
        The examples, oldest first, and the counts left out.
    """
    examples: list[VerdictExample] = []
    simulated = unrated = other_rubric = 0
    for one in receptions:
        label = labels.get((one.assignment_id, one.revision))
        if one.simulated:
            simulated += 1
        elif label is None or label.usable is None:
            unrated += 1
        elif label.source == RATING and label.rubric != rubric:
            other_rubric += 1
        else:
            examples.append(VerdictExample(one, label.usable, label.source))
    examples.sort(
        key=lambda one: (
            one.reception.started_at,
            one.reception.assignment_id,
            one.reception.revision,
        )
    )
    return VerdictExamples(tuple(examples), simulated, unrated, other_rubric)


def learns_from(route: str, examples: Sequence[VerdictExample]) -> list[Example]:
    """The examples a route's model can learn from: every one with its inputs."""
    rank = ROUTES.index(route)
    return [one.example for one in examples if ROUTES.index(one.route) <= rank]


def split_verdict_examples(
    examples: Sequence[VerdictExample],
    *,
    train_until: datetime,
    validate_until: datetime,
    as_of: datetime,
) -> VerdictSplit:
    """Split by date, as :func:`~meridian.prediction.splits.temporal_split` does.

    Raises:
        SplitError: The dates are out of order, or a reception started at or
            after ``as_of``, which a snapshot exported then cannot hold.
    """
    if not train_until < validate_until <= as_of:
        message = (
            f"the split needs train_until < validate_until <= as_of, and has"
            f" {train_until.isoformat()}, {validate_until.isoformat()},"
            f" {as_of.isoformat()}"
        )
        raise SplitError(message)
    late = [one for one in examples if one.reception.started_at >= as_of]
    if late:
        message = f"{len(late)} receptions started at or after as_of"
        raise SplitError(message)
    return VerdictSplit(
        train_until=train_until,
        validate_until=validate_until,
        as_of=as_of,
        train=_between(examples, None, train_until),
        validate=_between(examples, train_until, validate_until),
        test=_between(examples, validate_until, None),
    )


def verdict_counts(found: VerdictExamples, split: VerdictSplit) -> dict[str, int]:
    """Each span's size, usable and rated counts, and what was left out and why."""
    counts = {
        "examples.simulated": found.simulated,
        "examples.unrated": found.unrated,
        "examples.other_rubric": found.other_rubric,
    }
    for name, held in (
        ("train", split.train),
        ("validate", split.validate),
        ("test", split.test),
    ):
        counts[f"examples.{name}"] = len(held)
        counts[f"examples.{name}_usable"] = sum(one.usable for one in held)
        counts[f"examples.{name}_rated"] = sum(one.rated for one in held)
    return counts


def _between(
    examples: Sequence[VerdictExample], start: datetime | None, end: datetime | None
) -> tuple[VerdictExample, ...]:
    return tuple(
        one
        for one in examples
        if (start is None or one.reception.started_at >= start)
        and (end is None or one.reception.started_at < end)
    )

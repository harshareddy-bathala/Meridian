"""Judging a fitted verdict on its test span — SC-7's numbers (EVALUATION.md §11.1).

The test span is every labelled, measured reception from ``validate_until`` to
the snapshot's ``as_of``. Neither the fit nor its calibration saw it. Each
reception is scored by its own route, and judged twice (D-260):
- on **every labelled reception**, against the training span's usable rate;
- on **rated receptions only**, against the training span's rated usable rate.

The second leaves out the receptions with no product, whose label follows from
what the station reported. That is the half a verdict could get right by
reading the outcome alone.

**Segments** are §11.1's: station, band, data type, decoder version, and with
or without decoder statistics, because a verdict computed from less evidence
must still be calibrated. The archive segment §11.1 names is empty: an
archive reception carries no rating, so it has no label (D-262).

**The partial threshold is looked at on validation, never test.** For decoded
receptions in the validation span, the report gives how many fall each side
of ``partial_below`` and how many of each were usable. That is what an
operator reads to choose the threshold, before writing it into the
configuration. Choosing it on test would leak the answer into the number.

No fitting happens here, so this needs no ``fit`` extra.

Reference: docs/DECISIONS.md D-162, D-260, D-261, D-262.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.weighting import Rate, wilson
from meridian.prediction.calibration import Calibration, Scored, calibrate
from meridian.prediction.verdict_examples import (
    VerdictExample,
    VerdictExamples,
    VerdictSplit,
    split_verdict_examples,
)
from meridian.prediction.verdict_score import VerdictModel, score_reception

__all__ = [
    "VERDICT_DIMENSIONS",
    "Side",
    "VerdictEvaluation",
    "VerdictEvaluationError",
    "evaluate_verdict",
]

VERDICT_DIMENSIONS = (
    "station",
    "band",
    "data_type",
    "decoder_version",
    "decoder_statistics",
)
"""EVALUATION.md §11.1's segments, in the order the report lists them."""


class VerdictEvaluationError(ValueError):
    """A verdict that cannot be judged on this snapshot."""


@dataclass(frozen=True, slots=True)
class Side:
    """Decoded validation receptions on one side of the partial threshold."""

    n: int
    usable: Rate | None
    """``None`` when no reception falls on this side."""


@dataclass(frozen=True, slots=True)
class VerdictEvaluation:
    """SC-7's figures for one verdict model on one snapshot."""

    split: VerdictSplit
    every: Calibration
    rated: Calibration | None
    """``None`` when the test span holds no rated reception."""

    partial_below: float
    below: Side
    above: Side


def evaluate_verdict(
    model: VerdictModel,
    found: VerdictExamples,
    *,
    train_until: datetime,
    validate_until: datetime,
    as_of: datetime,
) -> VerdictEvaluation:
    """Score the test span and calibrate it, and read the threshold on validation.

    Raises:
        VerdictEvaluationError: The training span holds no labelled reception,
            or the test span none, so there is no base rate or nothing to judge.
        SplitError: The dates cannot split these receptions.
    """
    split = split_verdict_examples(
        found.examples,
        train_until=train_until,
        validate_until=validate_until,
        as_of=as_of,
    )
    if not split.train or not split.test:
        message = (
            f"training holds {len(split.train)} labelled receptions and test"
            f" {len(split.test)}; both need at least one to be judged"
        )
        raise VerdictEvaluationError(message)
    scored = [_scored(model, one) for one in split.test]
    every = calibrate(
        scored, base_rate=_rate(split.train), dimensions=VERDICT_DIMENSIONS
    )
    rated_train = [one for one in split.train if one.rated]
    rated_test = [
        held for held, one in zip(scored, split.test, strict=True) if one.rated
    ]
    rated = (
        calibrate(
            rated_test, base_rate=_rate(rated_train), dimensions=VERDICT_DIMENSIONS
        )
        if rated_train and rated_test
        else None
    )
    below, above = _threshold(model, split.validate)
    return VerdictEvaluation(
        split=split,
        every=every,
        rated=rated,
        partial_below=model.partial_below,
        below=below,
        above=above,
    )


def _scored(model: VerdictModel, one: VerdictExample) -> Scored:
    inputs = one.reception.inputs
    verdict = score_reception(model, inputs)
    decoder = (
        "none"
        if inputs.decoder is None
        else f"{inputs.decoder} {inputs.decoder_version or '?'}"
    )
    return Scored(
        probability=verdict.probability_usable,
        positive=one.usable,
        path=verdict.route,
        segments={
            "station": one.reception.station_id,
            "band": one.reception.band,
            "data_type": inputs.data_type,
            "decoder_version": decoder,
            "decoder_statistics": (
                "with" if inputs.has_decoder_statistics else "without"
            ),
        },
    )


def _threshold(
    model: VerdictModel, validate: Sequence[VerdictExample]
) -> tuple[Side, Side]:
    """Decoded validation receptions below and at-or-above ``partial_below``."""
    decoded = [one for one in validate if one.reception.inputs.outcome == "decoded"]
    below: list[VerdictExample] = []
    above: list[VerdictExample] = []
    for one in decoded:
        probability = score_reception(model, one.reception.inputs).probability_usable
        (below if probability < model.partial_below else above).append(one)
    return _side(below), _side(above)


def _side(members: Sequence[VerdictExample]) -> Side:
    if not members:
        return Side(n=0, usable=None)
    return Side(n=len(members), usable=wilson(_rate(members), len(members)))


def _rate(members: Sequence[VerdictExample]) -> float:
    return sum(one.usable for one in members) / len(members)

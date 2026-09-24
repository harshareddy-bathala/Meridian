"""Temporal splits, and the rolling-origin folds inside training — D-162.

**A split takes dates, never a shuffle.** An example is assigned by its
``aos`` alone:

* ``aos < train_until`` — training: the scaler and the coefficients;
* ``train_until <= aos < validate_until`` — validation: the calibration map;
* ``validate_until <= aos < as_of`` — test, read by evaluation only.

The examples may arrive in any order and the answer is the same, because no
position is read, only a date. There is no argument through which an example
after ``train_until`` could be put into training, and the gate's test checks
that rather than trusting it (``CLAUDE.md`` rule 6).

**Rolling-origin folds** cut the training span at evenly spaced origins. Fold
``k`` trains on everything before its origin and is judged on what follows,
up to the next origin; each fold's training span ends later than the one
before. The spread across folds is the variance a reported figure carries.

Reference: docs/DECISIONS.md D-162; docs/EVALUATION.md §8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from meridian.prediction.examples import Example

__all__ = ["Fold", "Split", "SplitError", "rolling_origin", "temporal_split"]


class SplitError(ValueError):
    """Dates that cannot split these examples as a time series."""


@dataclass(frozen=True, slots=True)
class Split:
    """The three spans, each in ``aos`` order, and the dates that made them."""

    train_until: datetime
    validate_until: datetime
    as_of: datetime
    train: tuple[Example, ...]
    validate: tuple[Example, ...]
    test: tuple[Example, ...]


@dataclass(frozen=True, slots=True)
class Fold:
    """One rolling-origin fold: train before ``origin``, judge up to ``until``."""

    origin: datetime
    until: datetime
    train: tuple[Example, ...]
    validate: tuple[Example, ...]


def temporal_split(
    examples: Sequence[Example],
    *,
    train_until: datetime,
    validate_until: datetime,
    as_of: datetime,
) -> Split:
    """Split examples by date.

    Args:
        examples: One population's examples, in any order.
        train_until: The first instant that is not training.
        validate_until: The first instant that is test.
        as_of: The dataset's export time; no example rises at or after it.

    Returns:
        The three spans.

    Raises:
        SplitError: The dates are out of order, or an example rises at or
            after ``as_of``, which a dataset cannot hold.
    """
    if not train_until < validate_until <= as_of:
        message = (
            f"the split needs train_until < validate_until <= as_of, and has"
            f" {train_until.isoformat()}, {validate_until.isoformat()},"
            f" {as_of.isoformat()}"
        )
        raise SplitError(message)
    ordered = _in_order(examples)
    late = [one for one in ordered if one.aos >= as_of]
    if late:
        message = (
            f"{len(late)} examples rise at or after as_of {as_of.isoformat()},"
            " which a dataset exported then cannot hold"
        )
        raise SplitError(message)
    return Split(
        train_until=train_until,
        validate_until=validate_until,
        as_of=as_of,
        train=tuple(one for one in ordered if one.aos < train_until),
        validate=tuple(
            one for one in ordered if train_until <= one.aos < validate_until
        ),
        test=tuple(one for one in ordered if validate_until <= one.aos),
    )


def rolling_origin(
    examples: Sequence[Example], *, train_until: datetime, folds: int
) -> tuple[Fold, ...]:
    """Folds with evenly spaced origins across the training span.

    Args:
        examples: One population's examples, in any order; those at or after
            ``train_until`` are not read.
        train_until: The end of the training span.
        folds: How many folds, at least one.

    Returns:
        The folds, earliest origin first. Empty when the span holds fewer than
        two distinct instants, since there is nothing to cut.

    Raises:
        SplitError: ``folds`` is below one.
    """
    if folds < 1:
        message = f"rolling-origin needs at least one fold, not {folds}"
        raise SplitError(message)
    training = [one for one in _in_order(examples) if one.aos < train_until]
    if not training or training[0].aos == training[-1].aos:
        return ()
    start = training[0].aos
    step = (train_until - start) / (folds + 1)
    origins = [start + step * k for k in range(1, folds + 2)]
    return tuple(
        Fold(
            origin=origin,
            until=until,
            train=tuple(one for one in training if one.aos < origin),
            validate=tuple(one for one in training if origin <= one.aos < until),
        )
        for origin, until in pairwise(origins)
    )


def _in_order(examples: Sequence[Example]) -> list[Example]:
    """By ``aos``, then station and satellite: the date decides, never position."""
    return sorted(examples, key=lambda one: (one.aos, one.station_id, one.satellite_id))

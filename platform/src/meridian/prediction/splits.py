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

**Rolling-origin folds are whole splits in miniature.** The span before
``validate_until`` is cut at evenly spaced origins, and fold ``j`` trains
before ``o_j``, calibrates on ``[o_j, o_j+1)`` and is judged on
``[o_j+1, o_j+2)`` — the three spans of the main split, each fold's ending
later than the one before. No fold reads the test span, and the spread of a
figure across folds is the variance it carries (D-162).

Reference: docs/DECISIONS.md D-162; docs/EVALUATION.md §8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.prediction.examples import Example

__all__ = ["Split", "SplitError", "rolling_origin", "temporal_split"]


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
    examples: Sequence[Example], *, until: datetime, folds: int
) -> tuple[Split, ...]:
    """Splits with evenly spaced origins across the span before ``until``.

    Args:
        examples: One population's examples, in any order; those at or after
            ``until`` are not read.
        until: The end of the span folded — the configuration's
            ``validate_until``, so the test span is never read.
        folds: How many folds, at least one.

    Returns:
        The folds as splits, earliest first; fold ``j`` trains before
        ``o_j``, calibrates up to ``o_j+1`` and is judged up to ``o_j+2``.
        Empty when the span holds fewer than two distinct instants, since
        there is nothing to cut.

    Raises:
        SplitError: ``folds`` is below one.
    """
    if folds < 1:
        message = f"rolling-origin needs at least one fold, not {folds}"
        raise SplitError(message)
    before = [one for one in _in_order(examples) if one.aos < until]
    if not before or before[0].aos == before[-1].aos:
        return ()
    start = before[0].aos
    step = (until - start) / (folds + 2)
    origins = [start + step * k for k in range(1, folds + 3)]
    origins[-1] = until
    return tuple(
        temporal_split(
            [one for one in before if one.aos < origins[j + 2]],
            train_until=origins[j],
            validate_until=origins[j + 1],
            as_of=origins[j + 2],
        )
        for j in range(folds)
    )


def _in_order(examples: Sequence[Example]) -> list[Example]:
    """By ``aos``, then station and satellite: the date decides, never position."""
    return sorted(examples, key=lambda one: (one.aos, one.station_id, one.satellite_id))

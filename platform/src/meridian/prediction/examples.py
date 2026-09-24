"""The training examples of one population — D-156.

**Our stations.** An example is a labelled physical pass with a yield label:
``successful_reception`` is positive, ``signal_no_decode`` and
``confirmed_miss`` are negative (``USABLE_LABELS``, D-149). Every other label
and every exclusion stays out — a pass nobody listened for says nothing about
whether it would have decoded, and a silent satellite says nothing about the
station (``EVALUATION.md`` §5). Its features are every feature of D-157 to
D-159, and it carries its station's settled record so the cold-start route can
be taken (D-161).

**The archive.** An example is a computed archive pass (D-150) that a
reception was matched to with an outcome: ``decoded`` is positive, ``no_data``
negative, ``unknown`` out — the same rule completeness uses (D-153). An
archive pass carries its peak elevation and nothing else we compute, so that
is its one feature, and the model configuration refuses anything but A for it.

**Simulated passes are counted, never examples** (D-078). The counts travel
with the examples, so fitting can refuse a dataset whose usable passes are all
simulated with that reason, not with "too few examples".

Examples are in ``aos`` order, then station, then pass: the order a temporal
split reads them in.

Reference: docs/DECISIONS.md D-078, D-149, D-150, D-153, D-156, D-161.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.archive_matching import match_receptions
from meridian.datasets.completeness import USABLE_LABELS
from meridian.datasets.labels import LabelledPass
from meridian.datasets.snapshot_rows import SnapshotRows
from meridian.prediction.feature_rows import FeatureRows
from meridian.prediction.features import compute_features
from meridian.prediction.history import History, events_of
from meridian.prediction.profiles import Environment

__all__ = ["Example", "ExampleSet", "archive_examples", "own_examples"]

_SUCCESS = "successful_reception"


@dataclass(frozen=True, slots=True)
class Example:
    """One pass with a yield outcome, and what was knowable before it."""

    population: str
    station_id: str
    satellite_id: str
    aos: datetime
    positive: bool
    features: Mapping[str, float]
    station_history: int
    """Settled, usable outcomes at the station before ``aos`` (D-161)."""


@dataclass(frozen=True, slots=True)
class ExampleSet:
    """A population's examples, and how many usable passes were simulated."""

    population: str
    examples: tuple[Example, ...]
    simulated: int
    """Usable passes left out because they were simulated (D-078)."""


def own_examples(
    labelled: Sequence[LabelledPass], rows: FeatureRows, *, settle_margin_s: int
) -> ExampleSet:
    """Our stations' examples, each with every feature at its own ``aos``.

    Args:
        labelled: Every labelled pass of the dataset; all of them are history,
            and the usable, measured ones are examples.
        rows: The raw snapshot's feature rows.
        settle_margin_s: The labelling configuration's margin (D-146).

    Returns:
        The examples, in ``aos`` order, and the simulated count.
    """
    usable = [one for one in labelled if one.label in USABLE_LABELS]
    measured = sorted(
        (one for one in usable if not one.simulated),
        key=lambda one: (one.aos, one.station_id, one.pass_id),
    )
    history = History(
        events_of(labelled, bands=rows.bands, settle_margin_s=settle_margin_s)
    )
    environment = Environment(labelled, rows, settle_margin_s=settle_margin_s)
    vectors = compute_features(measured, rows, history, environment)
    return ExampleSet(
        population="own",
        examples=tuple(
            Example(
                population="own",
                station_id=one.station_id,
                satellite_id=one.satellite_id,
                aos=one.aos,
                positive=one.label == _SUCCESS,
                features=vector.named(),
                station_history=history.decode_rate(
                    ("station", one.station_id), one.aos
                ).trials,
            )
            for one, vector in zip(measured, vectors, strict=True)
        ),
        simulated=len(usable) - len(measured),
    )


def archive_examples(
    rows: SnapshotRows, *, tolerance_s: int, since: datetime
) -> ExampleSet:
    """The archive's examples: matched receptions with an outcome, from ``since``.

    Args:
        rows: The raw snapshot's rows: the computed archive passes and the
            receptions.
        tolerance_s: The labelling configuration's match tolerance (D-150).
        since: The snapshot's start; a pass rising before it places a
            reception and is no example, as it is in no ratio (D-148).

    Returns:
        The examples, in ``aos`` order. The archive holds no simulated rows.
    """
    matches = match_receptions(rows.archive_passes, rows.archive, tolerance_s)
    usable = sorted(
        (one for one in matches.usable if one.aos >= since),
        key=lambda one: (one.aos, one.archive_station_id, one.satellite_id),
    )
    return ExampleSet(
        population="archive",
        examples=tuple(
            Example(
                population="archive",
                station_id=f"archive:{one.archive_station_id}",
                satellite_id=one.satellite_id,
                aos=one.aos,
                positive=one in matches.succeeded,
                features={"max_elevation_deg": one.max_elevation_deg},
                station_history=0,
            )
            for one in usable
        ),
        simulated=0,
    )

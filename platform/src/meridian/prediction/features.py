"""Each labelled pass as numbers a model can read — geometry and station history.

Every feature is a pure function of the raw snapshot, the labels and the pass's
own ``aos``: geometry from the pass's representative prediction (D-148), and
history from :class:`~meridian.prediction.history.History`, which answers only
with outcomes settled before the pass began (D-157).

**Every feature has a value for every pass.** No NaN: an angle is
written as its sine and cosine so north is not a discontinuity; a rate with no
history is one half, beside a count of zero; a pass whose track the export
could not compute takes its peak and sweep from its rise and set azimuths, and
says so in ``track_known``; a published condition that is missing is 0 beside a
``known`` of 0 (D-224). What a model does with a thin history is for the
model to learn from the counts, or for the cold-start path (D-161) to decide —
never for a NaN to decide by accident.

**Each feature belongs to a group**, which is how configurations choose inputs
(D-160): ``elevation`` is configuration A's one input; ``geometry`` is the rest
of what the orbit says; ``ours`` is what EVALUATION.md §2 marks as ours —
element-set age, the station's own record, and the learned environment of
D-159 from :mod:`meridian.prediction.profiles`; ``conditions`` is what public
sources published before the pass (:mod:`meridian.prediction.conditions`).

**The feature code has a version of its own**, ``FEATURE_VERSION``, written
into every model it fits. A model is scored only by the code it was fitted on:
features computed differently would feed its coefficients numbers it never
learned from, and nothing about the score would say so (D-255).

Reference: docs/DECISIONS.md D-148, D-157, D-159, D-160, D-161, D-255.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.labels import LabelledPass
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.conditions import CONDITION_FEATURES
from meridian.prediction.feature_rows import FeatureRows, PassGeometry
from meridian.prediction.geometry import circle, peak_and_sweep
from meridian.prediction.history import RECENT, History, Rate
from meridian.prediction.profiles import ENVIRONMENT, Environment
from meridian.prediction.score import MalformedModelError, Model

__all__ = [
    "FEATURES",
    "FEATURE_VERSION",
    "RECENT",
    "Feature",
    "FeatureVector",
    "compute_features",
    "require_current_features",
]

FEATURE_VERSION = "features-1"
"""The version of the code that turns a pass into features: this module and
what it reads in :mod:`meridian.prediction`. Bumped with any change to what a
feature's value would be; ``tests/unit/test_feature_version.py`` fails until it
is (D-255)."""


@dataclass(frozen=True, slots=True)
class Feature:
    """One named input, the group that selects it, and what it means."""

    name: str
    group: str
    meaning: str


FEATURES: tuple[Feature, ...] = (
    Feature("max_elevation_deg", "elevation", "the pass's peak elevation"),
    Feature("duration_min", "geometry", "aos to los, in minutes"),
    Feature("aos_azimuth_sin", "geometry", "sine of the rise azimuth"),
    Feature("aos_azimuth_cos", "geometry", "cosine of the rise azimuth"),
    Feature("los_azimuth_sin", "geometry", "sine of the set azimuth"),
    Feature("los_azimuth_cos", "geometry", "cosine of the set azimuth"),
    Feature("peak_azimuth_sin", "geometry", "sine of the azimuth at the peak"),
    Feature("peak_azimuth_cos", "geometry", "cosine of the azimuth at the peak"),
    Feature("azimuth_sweep_deg", "geometry", "azimuth travelled, rise to set"),
    Feature("track_known", "geometry", "1 if export froze a track, else 0"),
    Feature("element_set_age_h", "ours", "hours from the set's epoch to aos"),
    Feature("station_decode_rate", "ours", f"last {RECENT} usable, shrunk"),
    Feature("station_decode_n", "ours", "how many that rate is over"),
    Feature("satellite_decode_rate", "ours", "this satellite here, shrunk"),
    Feature("satellite_decode_n", "ours", "how many that rate is over"),
    Feature("band_decode_rate", "ours", "this band here, shrunk"),
    Feature("band_decode_n", "ours", "how many that rate is over"),
    Feature("station_availability", "ours", f"last {RECENT} scheduled taken up"),
    Feature("station_availability_n", "ours", "how many that share is over"),
    *(Feature(name, "ours", meaning) for name, meaning in ENVIRONMENT),
    *(Feature(name, "conditions", meaning) for name, meaning in CONDITION_FEATURES),
)


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """One pass's features, in :data:`FEATURES` order."""

    pass_id: int
    station_id: str
    satellite_id: str
    aos: datetime
    values: tuple[float, ...]

    def named(self) -> dict[str, float]:
        """The values by feature name."""
        return {
            one.name: value for one, value in zip(FEATURES, self.values, strict=True)
        }


def compute_features(
    labelled: Sequence[LabelledPass],
    rows: FeatureRows,
    history: History,
    environment: Environment,
) -> tuple[FeatureVector, ...]:
    """The features of each pass, at its own ``aos``.

    Args:
        labelled: The passes to describe, as the evaluation dataset holds them.
        rows: The raw snapshot's geometry and bands.
        history: Every settled event, which answers only for the past.
        environment: Every settled report placed on the sky, likewise.

    Returns:
        One vector per pass, in the order given.

    Raises:
        MalformedSnapshotError: A pass's representative prediction is not in
            the raw snapshot, so the labels and the snapshot do not belong
            together.
    """
    return tuple(_vector(one, rows, history, environment) for one in labelled)


def _vector(
    one: LabelledPass, rows: FeatureRows, history: History, environment: Environment
) -> FeatureVector:
    geometry = rows.geometry.get(one.pass_id)
    if geometry is None:
        message = (
            f"pass {one.pass_id} is labelled but not in the raw snapshot;"
            " the dataset and the snapshot are not a pair"
        )
        raise MalformedSnapshotError(message)
    band = rows.bands.get(one.satellite_id, "unknown")
    at = one.aos
    values = (
        *_geometry(one, geometry),
        (at - geometry.element_set_epoch).total_seconds() / 3600.0,
        *_rate(history.decode_rate(("station", one.station_id), at, recent=RECENT)),
        *_rate(
            history.decode_rate(("satellite", one.station_id, one.satellite_id), at)
        ),
        *_rate(history.decode_rate(("band", one.station_id, band), at)),
        *_rate(history.availability(one.station_id, at, recent=RECENT)),
        *environment.values(one, geometry),
        *rows.conditions.values(one.station_id, at),
    )
    return FeatureVector(
        pass_id=one.pass_id,
        station_id=one.station_id,
        satellite_id=one.satellite_id,
        aos=at,
        values=values,
    )


def _geometry(one: LabelledPass, geometry: PassGeometry) -> tuple[float, ...]:
    peak, sweep = peak_and_sweep(geometry)
    return (
        geometry.max_elevation_deg,
        (one.los - one.aos).total_seconds() / 60.0,
        *circle(geometry.aos_azimuth_deg),
        *circle(geometry.los_azimuth_deg),
        *circle(peak),
        sweep,
        0.0 if geometry.track is None or not geometry.track.azimuth_deg else 1.0,
    )


def _rate(rate: Rate) -> tuple[float, float]:
    return rate.smoothed, float(rate.trials)


def require_current_features(model: Model) -> None:
    """Refuse a model fitted on other feature code than this.

    Raises:
        MalformedModelError: The model names another feature version.
    """
    if model.feature_version != FEATURE_VERSION:
        message = (
            f"the model was fitted on {model.feature_version} and this code"
            f" computes {FEATURE_VERSION}; fit it again"
        )
        raise MalformedModelError(message)

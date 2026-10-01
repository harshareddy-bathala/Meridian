"""The prediction section's rows: every model's calibration, and what they add.

For each variant the report fitted, the rows ``EVALUATION.md`` §7 says a model
ships with — the Brier score against the training base rate, the reliability
diagram's bins, calibration by station, band and element-set age, and the
cold-start routes (D-161, D-164) — with the split dates and the counts behind
them (§8), and the rolling-origin folds. A variant that could not be fitted is
one row saying why.

**Every interval is a station-day bootstrap** (:mod:`meridian.reports.
bootstrap`): the Brier score, the skill against the base rate — SC-2's Brier
reduction — and each comparison between two models on the same test passes:

* **D against D∖conditions** — what the public conditions add (§3);
* **C against A** — whether our signals carry information elevation does not;
* **D against A** — what everything adds over elevation.

**The Kp feature is untested below a stated count of disturbed passes** (§3).
The count, the threshold and the minimum are in the conditions row, so a reader
sees the sample the verdict rests on. Cloud cover varies daily and has no such
risk, so its count is stated beside it and never folded into one verdict.

**Reals are rounded to six decimal places.** A fit agrees across numerical
environments to about 1e-9 (D-163); the published figure should not change
with the last bits of a library, and three places are printed.

Reference: docs/DECISIONS.md D-161, D-163, D-164, D-224, D-237.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import fsum

from meridian.datasets.weighting import Rate
from meridian.prediction.calibration import Calibration
from meridian.prediction.lineage import Inputs
from meridian.prediction.model_config import model_config_sha256
from meridian.reports.bootstrap import Bootstrapped, cluster_bootstrap, clustered
from meridian.reports.config import PredictionConfig
from meridian.reports.prediction import Fitted, Judged, Refused

__all__ = ["COMPARISONS", "PREDICTION_FILE", "SC2_TARGET", "prediction_rows"]

PREDICTION_FILE = "prediction.jsonl"

COMPARISONS = (
    ("D", "D-conditions", "what the public conditions add"),
    ("C", "A", "whether our signals carry information elevation does not"),
    ("D", "A", "what everything adds over elevation"),
)

SC2_TARGET = 0.25
"""SC-2: a Brier score at least 25% below the base rate's (``EVALUATION.md`` §1)."""

_PLACES = 6
_KP, _KP_KNOWN, _CLOUD_KNOWN = "kp_index", "kp_known", "cloud_cover_known"

Row = dict[str, object]


def prediction_rows(
    outcomes: Sequence[Fitted | Refused],
    inputs: Inputs | None,
    config: PredictionConfig,
    *,
    seed: int,
) -> list[Row]:
    """Every row of the section, in variant order, then what compares them.

    Args:
        outcomes: Each variant fitted or refused, as ``fit_variants`` gave them.
        inputs: The examples, or ``None`` when the dataset gave none.
        config: The ``[prediction]`` settings.
        seed: The bootstrap's seed, derived as ``bootstrap.prediction``.
    """
    fitted = {one.variant.name: one for one in outcomes if isinstance(one, Fitted)}
    rows: list[Row] = []
    for one in outcomes:
        rows.extend(
            _fitted_rows(one, seed, config.resamples)
            if isinstance(one, Fitted)
            else [_refused_row(one)]
        )
    rows.extend(
        _comparison(fitted, pair, seed, config.resamples) for pair in COMPARISONS
    )
    rows.append(_conditions(fitted, inputs, config))
    rows.append(_sc2(rows))
    return rows


def _refused_row(one: Refused) -> Row:
    return {
        "row": "model",
        "name": one.variant.name,
        "configuration": one.variant.configuration,
        "without": list(one.variant.without),
        "status": "refused",
        "reason": one.reason,
        "seed": None if one.config is None else one.config.seed,
    }


def _fitted_rows(one: Fitted, seed: int, resamples: int) -> list[Row]:
    evaluation = one.evaluation
    calibration = evaluation.calibration
    split = evaluation.split
    drawn = _intervals(one.judged, calibration.base_rate, seed, resamples)
    folds = evaluation.fold_brier
    model: Row = {
        "row": "model",
        "name": one.variant.name,
        "configuration": one.variant.configuration,
        "without": list(one.variant.without),
        "status": "fitted",
        "seed": one.config.seed,
        "population": one.config.population,
        "model_sha256": one.sha256,
        "config_sha256": model_config_sha256(one.config),
        "train_until": split.train_until,
        "validate_until": split.validate_until,
        "as_of": split.as_of,
        "train": len(split.train),
        "validate": len(split.validate),
        "test": calibration.n,
        "test_decoded": calibration.decoded,
        "station_days": len(clustered(one.judged, _day)),
        "simulated_left_out": evaluation.simulated,
        "without_weight": evaluation.without_weight,
        "brier": _real(calibration.brier),
        "brier_interval": _interval(drawn["brier"]),
        "base_rate": _real(calibration.base_rate),
        "base_brier": _real(calibration.base_brier),
        "skill": _maybe(calibration.skill),
        "skill_interval": _interval(drawn["skill"]),
        "skill_dropped": drawn["skill"].dropped,
        "resamples": resamples,
        "folds": one.config.folds,
        "folds_fitted": sum(1 for fold in evaluation.folds if fold.brier is not None),
        "fold_brier_mean": None if folds is None else _real(folds[0]),
        "fold_brier_sd": None if folds is None else _maybe(folds[1]),
    }
    name = one.variant.name
    folds_rows: list[Row] = [
        {
            "row": "fold",
            "model": name,
            "train_until": fold.train_until,
            "validate_until": fold.validate_until,
            "as_of": fold.as_of,
            "n": fold.n,
            "brier": _maybe(fold.brier),
            "base_brier": _maybe(fold.base_brier),
            "refused": fold.refused,
        }
        for fold in evaluation.folds
    ]
    return [model, *_calibration_rows(name, calibration), *folds_rows]


def _calibration_rows(name: str, calibration: Calibration) -> list[Row]:
    """The reliability diagram, the segments and the cold-start routes."""
    bins: list[Row] = [
        {
            "row": "bin",
            "model": name,
            "low": _real(one.low),
            "high": _real(one.high),
            "n": one.n,
            "mean_predicted": _maybe(one.mean_predicted),
            "observed": _rate(one.observed),
        }
        for one in calibration.bins
    ]
    segments: list[Row] = [
        {
            "row": "segment",
            "model": name,
            "dimension": one.dimension,
            "value": one.value,
            "n": one.n,
            "brier": _real(one.brier),
            "mean_predicted": _real(one.mean_predicted),
            "observed": _rate(one.observed),
        }
        for one in calibration.segments
    ]
    routes: list[Row] = [
        {"row": "route", "model": name, "path": one.path, "n": one.n}
        | {"brier": _real(one.brier)}
        for one in calibration.routes
    ]
    return [*bins, *segments, *routes]


def _intervals(
    judged: Sequence[Judged], base_rate: float, seed: int, resamples: int
) -> dict[str, Bootstrapped]:
    """The Brier score's interval and the skill's, from one set of draws."""

    def skill(drawn: Sequence[Judged]) -> float | None:
        base = _brier(drawn, lambda _: base_rate)
        return None if base == 0 else 1.0 - _brier(drawn, _said) / base

    return cluster_bootstrap(
        clustered(judged, _day),
        {"brier": lambda drawn: _brier(drawn, _said), "skill": skill},
        seed=seed,
        resamples=resamples,
    )


def _comparison(
    fitted: Mapping[str, Fitted],
    pair: tuple[str, str, str],
    seed: int,
    resamples: int,
) -> Row:
    """How much lower the first model's Brier score is than the second's."""
    first, second, question = pair
    row: Row = {"row": "comparison", "first": first, "second": second}
    row["question"] = question
    if first not in fitted or second not in fitted:
        missing = [name for name in (first, second) if name not in fitted]
        return row | {"status": "not compared", "reason": f"{missing} not fitted"}
    paired = list(zip(fitted[first].judged, fitted[second].judged, strict=True))

    def reduction(drawn: Sequence[tuple[Judged, Judged]]) -> float:
        return fsum(
            (two.probability - two.positive) ** 2
            - (one.probability - one.positive) ** 2
            for one, two in drawn
        ) / len(drawn)

    drawn = cluster_bootstrap(
        clustered(paired, lambda both: both[0].station_day),
        {"reduction": reduction},
        seed=seed,
        resamples=resamples,
    )["reduction"]
    return row | {
        "status": "compared",
        "n": len(paired),
        "station_days": len(clustered(paired, lambda both: both[0].station_day)),
        "first_brier": _real(_brier(fitted[first].judged, _said)),
        "second_brier": _real(_brier(fitted[second].judged, _said)),
        "reduction": _real(reduction(paired)),
        "interval": _interval(drawn),
        "resamples": resamples,
    }


def _conditions(
    fitted: Mapping[str, Fitted], inputs: Inputs | None, config: PredictionConfig
) -> Row:
    """How many test passes could say anything about Kp and about cloud."""
    row: Row = {
        "row": "conditions",
        "disturbed_kp": float(config.disturbed_kp),
        "min_disturbed": config.min_disturbed,
    }
    judged = fitted.get("D") or next(iter(fitted.values()), None)
    if judged is None or inputs is None:
        return row | {"kp_verdict": "untested", "reason": "no model was fitted"}
    test = judged.evaluation.split.test
    known = [one for one in test if one.features.get(_KP_KNOWN) == 1.0]
    disturbed = sum(1 for one in known if one.features[_KP] >= config.disturbed_kp)
    return row | {
        "test_passes": len(test),
        "kp_known": len(known),
        "disturbed": disturbed,
        "cloud_known": sum(1 for one in test if one.features.get(_CLOUD_KNOWN) == 1.0),
        "kp_verdict": "measurable" if disturbed >= config.min_disturbed else "untested",
    }


def _sc2(rows: Sequence[Row]) -> Row:
    """SC-2, read from D's skill: the point estimate and its whole interval."""
    shipped = next(
        (one for one in rows if one["row"] == "model" and one["name"] == "D"), None
    )
    row: Row = {"row": "sc2", "model": "D", "target": SC2_TARGET}
    if shipped is None or shipped["status"] != "fitted":
        return row | {"status": "not measured", "reason": "D was not fitted"}
    interval = shipped["skill_interval"]
    skill = shipped["skill"]
    return row | {
        "status": "measured",
        "skill": skill,
        "interval": interval,
        "n": shipped["test"],
        "station_days": shipped["station_days"],
        "point_meets": isinstance(skill, float) and skill >= SC2_TARGET,
        "interval_above": isinstance(interval, dict) and interval["low"] >= SC2_TARGET,
    }


def _said(one: Judged) -> float:
    return one.probability


def _day(one: Judged) -> tuple[str, str]:
    return one.station_day


def _brier(judged: Sequence[Judged], said: Callable[[Judged], float]) -> float:
    return fsum((said(one) - one.positive) ** 2 for one in judged) / len(judged)


def _interval(drawn: Bootstrapped) -> dict[str, float] | None:
    if drawn.interval is None:
        return None
    return {"low": _real(drawn.interval.low), "high": _real(drawn.interval.high)}


def _rate(rate: Rate | None) -> dict[str, float] | None:
    if rate is None:
        return None
    return {
        "estimate": _real(rate.estimate),
        "low": _real(rate.low),
        "high": _real(rate.high),
        "n": rate.n,
    }


def _real(value: float) -> float:
    return round(value, _PLACES)


def _maybe(value: float | None) -> float | None:
    return None if value is None else _real(value)

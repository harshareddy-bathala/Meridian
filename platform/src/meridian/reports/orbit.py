"""The orbit-uncertainty section: timing error, element-set age, and SC-3.

``EVALUATION.md`` §6 measures orbital data quality by timing error
(:mod:`meridian.reports.detections`). The roadmap asks for four things, and
each population — measured and simulated — gets its own, never pooled:

* **error against element-set age, by orbital regime** — an ordinary
  least-squares slope of ``|timing error|`` on age in days, over the passes
  §6.1 keeps, with a station-day bootstrap interval;
* **1σ empirical coverage** — the share of passes whose ``|timing error|`` is
  inside the uncertainty the station was given, with a Wilson interval. That is
  SC-3, read from measured passes only (≥ 68%). §6.1 discards an error smaller
  than the clock's uncertainty, and such an error is inside any stated 1σ, so
  the coverage is also given with only the unknown-offset passes left out;
  the two bound the effect of the rule (D-239);
* **clock-uncertainty exclusions** — counted by reason, never silently applied;
* **§6.3's spread test** (D-100) — on element sets under a day old, where the
  orbit contributes a fraction of a second, how widely first detection still
  spreads. If that spread exceeds the orbital signal the data could show —
  §6.3's 0.27 s per day of age over the span of ages present — timing error
  measures the station's horizon rather than the element set, and the section
  says §6.1 is **not fit as written**. Below a stated number of young
  detections it is **not tested**.

Reference: docs/DECISIONS.md D-025, D-100, D-177, D-239; ``EVALUATION.md`` §6.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum
from statistics import stdev

from meridian.datasets.weighting import wilson
from meridian.orbit.uncertainty import timing_uncertainty_at_age
from meridian.reports.bootstrap import Bootstrapped, cluster_bootstrap, clustered
from meridian.reports.config import OrbitConfig
from meridian.reports.detections import Detection

__all__ = ["ORBIT_FILE", "SC3_TARGET", "SIGNAL_S_PER_DAY", "orbit_rows"]

ORBIT_FILE = "orbit.jsonl"

SC3_TARGET = 0.68
"""SC-3: at least 68% of passes within their stated 1σ (``EVALUATION.md`` §1)."""

SIGNAL_S_PER_DAY = 0.27
"""The most timing an element set loses per day of age: about 2 km a day along
track at 7.5 km/s (``EVALUATION.md`` §6.3)."""

_YOUNG_DAYS = 1.0
_PRIOR_STEPS = 24
_PLACES = 6
POPULATIONS = ("measured", "simulated")

Row = dict[str, object]


def orbit_rows(
    found: Sequence[Detection], config: OrbitConfig, *, seed: int
) -> list[Row]:
    """Every row of the section: per population, per regime, then SC-3.

    Args:
        found: Every detection in the snapshot, both populations.
        config: The ``[orbit]`` settings.
        seed: The bootstrap's seed, derived as ``bootstrap.orbit``.
    """
    rows: list[Row] = []
    for population in POPULATIONS:
        mine = [one for one in found if one.simulated == (population == "simulated")]
        rows.append(_counts(population, mine))
        kept = [one for one in mine if one.excluded is None]
        rows.extend(
            _regime(population, regime, [one for one in kept if one.regime == regime])
            | _slope_interval(
                [one for one in kept if one.regime == regime], config, seed
            )
            for regime in sorted({one.regime for one in kept})
        )
        rows.append(_coverage(population, mine))
        rows.append(_spread(population, kept, config))
        rows.extend(_points(population, mine))
    rows.extend(_prior(found))
    rows.append(_sc3(rows))
    return rows


def _counts(population: str, mine: Sequence[Detection]) -> Row:
    reasons = [one.excluded for one in mine]
    return {
        "row": "detections",
        "population": population,
        "detections": len(mine),
        "clock_offset_unknown": reasons.count("clock_offset_unknown"),
        "within_clock_uncertainty": reasons.count("within_clock_uncertainty"),
        "kept": reasons.count(None),
        "sigma_from_assignment": sum(
            1 for one in mine if one.sigma_source == "assignment"
        ),
        "sigma_from_prior": sum(1 for one in mine if one.sigma_source == "prior"),
    }


def _regime(population: str, regime: str, kept: Sequence[Detection]) -> Row:
    row: Row = {"row": "regime", "population": population, "regime": regime}
    ages = [one.element_set_age_days for one in kept]
    fitted = _slope(kept)
    return row | {
        "n": len(kept),
        "station_days": len(clustered(kept, _station_day)),
        "age_min_days": _real(min(ages)),
        "age_max_days": _real(max(ages)),
        "mean_abs_error_s": _real(fsum(abs(_error(one)) for one in kept) / len(kept)),
        "slope_s_per_day": None if fitted is None else _real(fitted[0]),
        "intercept_s": None if fitted is None else _real(fitted[1]),
    }


def _slope_interval(kept: Sequence[Detection], config: OrbitConfig, seed: int) -> Row:
    """The slope's station-day bootstrap interval, or why there is none."""
    if _slope(kept) is None:
        return {"slope_interval": None, "reason": "fewer than 3 passes, or one age"}
    drawn = cluster_bootstrap(
        clustered(kept, _station_day),
        {"slope": _slope_only},
        seed=seed,
        resamples=config.resamples,
    )["slope"]
    return {"slope_interval": _interval(drawn), "slope_dropped": drawn.dropped}


def _coverage(population: str, mine: Sequence[Detection]) -> Row:
    """1σ coverage under §6.1's exclusions, and with the unknown offsets only."""
    kept = [one for one in mine if one.excluded is None]
    known = [one for one in mine if one.error_s is not None]
    return {
        "row": "coverage",
        "population": population,
        "kept": _within(kept),
        "offset_known": _within(known),
    }


def _within(passes: Sequence[Detection]) -> Row | None:
    if not passes:
        return None
    inside = sum(1 for one in passes if abs(_error(one)) <= one.sigma_s)
    rate = wilson(inside / len(passes), len(passes))
    return {
        "within": inside,
        "n": len(passes),
        "estimate": _real(rate.estimate),
        "low": _real(rate.low),
        "high": _real(rate.high),
    }


def _spread(population: str, kept: Sequence[Detection], config: OrbitConfig) -> Row:
    """§6.3: does first detection spread more than the orbit could move it?"""
    young = [one for one in kept if abs(one.element_set_age_days) < _YOUNG_DAYS]
    ages = [one.element_set_age_days for one in kept]
    signal = SIGNAL_S_PER_DAY * (max(ages) - min(ages)) if ages else 0.0
    row: Row = {
        "row": "spread",
        "population": population,
        "young": len(young),
        "min_young": config.min_young,
        "signal_s": _real(signal),
        "signal_s_per_day": SIGNAL_S_PER_DAY,
    }
    if len(young) < config.min_young:
        return row | {"spread_s": None, "verdict": "not tested"}
    spread = stdev(_error(one) for one in young)
    fit = "fit" if spread <= signal else "not fit as written"
    return row | {"spread_s": _real(spread), "verdict": fit}


def _points(population: str, mine: Sequence[Detection]) -> list[Row]:
    """Every detection, for the figure and for a reader to recompute from."""
    return [
        {
            "row": "detection",
            "population": population,
            "assignment_id": one.assignment_id,
            "station_id": one.station_id,
            "regime": one.regime,
            "aos": one.aos,
            "age_days": _real(one.element_set_age_days),
            "uncorrected_s": _real(one.uncorrected_s),
            "error_s": None if one.error_s is None else _real(one.error_s),
            "sigma_s": _real(one.sigma_s),
            "sigma_source": one.sigma_source,
            "excluded": one.excluded,
        }
        for one in mine
    ]


def _prior(found: Sequence[Detection]) -> list[Row]:
    """The published 1σ prior across the ages present, for the figure."""
    ages = [one.element_set_age_days for one in found]
    if not ages:
        return []
    top = max(*ages, 1.0)
    return [
        {
            "row": "prior",
            "age_days": _real(top * step / _PRIOR_STEPS),
            "sigma_s": _real(
                timing_uncertainty_at_age(top * step / _PRIOR_STEPS * 86_400).sigma_s
            ),
        }
        for step in range(_PRIOR_STEPS + 1)
    ]


def _sc3(rows: Sequence[Row]) -> Row:
    """SC-3 from measured coverage under §6.1: the point, and its interval."""
    coverage = next(
        one
        for one in rows
        if one["row"] == "coverage" and one["population"] == "measured"
    )["kept"]
    row: Row = {"row": "sc3", "target": SC3_TARGET}
    if not isinstance(coverage, dict):
        return row | {"status": "not measured", "reason": "no measured pass is kept"}
    return row | {
        "status": "measured",
        "coverage": coverage["estimate"],
        "interval": {"low": coverage["low"], "high": coverage["high"]},
        "n": coverage["n"],
        "point_meets": float(str(coverage["estimate"])) >= SC3_TARGET,
        "interval_above": float(str(coverage["low"])) >= SC3_TARGET,
    }


def _error(one: Detection) -> float:
    error = one.error_s
    if error is None:
        message = f"{one.assignment_id} has no corrected error; it was not kept"
        raise ValueError(message)
    return error


def _slope(kept: Sequence[Detection]) -> tuple[float, float] | None:
    """OLS of |error| on age: slope and intercept, or ``None`` if undefined."""
    if len(kept) < 3:  # noqa: PLR2004 — a line through two points has no residual
        return None
    ages = [one.element_set_age_days for one in kept]
    errors = [abs(_error(one)) for one in kept]
    mean_age, mean_error = fsum(ages) / len(ages), fsum(errors) / len(errors)
    spread = fsum((age - mean_age) ** 2 for age in ages)
    if spread == 0:
        return None
    slope = (
        fsum(
            (age - mean_age) * (error - mean_error)
            for age, error in zip(ages, errors, strict=True)
        )
        / spread
    )
    return slope, mean_error - slope * mean_age


def _slope_only(drawn: Sequence[Detection]) -> float | None:
    fitted = _slope(drawn)
    return None if fitted is None else fitted[0]


def _station_day(one: Detection) -> tuple[str, str]:
    return one.station_id, one.aos.date().isoformat()


def _interval(drawn: Bootstrapped) -> dict[str, float] | None:
    if drawn.interval is None:
        return None
    return {"low": _real(drawn.interval.low), "high": _real(drawn.interval.high)}


def _real(value: float) -> float:
    return round(value, _PLACES)

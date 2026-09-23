"""Who was selected, and how the selection is corrected for — Stage 16 in one call.

:func:`select` takes the labelled passes and the raw snapshot's rows and
produces everything :mod:`meridian.datasets.evaluation` writes about selection:

* the **station-days** of both populations (D-149, D-150);
* one **candidate** per eligible pass — our eligible physical passes, and the
  archive's computed passes on the days its station was active, at or above
  the elevation floor — with the policy's decision and nothing about the
  outcome (D-152);
* each candidate's **propensity**, from one estimate over both populations,
  whose cells never mix them;
* per population, an :class:`~meridian.datasets.result.EvaluationResult`:
  its completeness summary, and its weights' diagnostics (D-153) or the
  reason it has none.

The outcome is joined to an estimate only after the estimate is made. It
travels beside the candidate, never inside it, which is how D-152's "never
sees an outcome" holds here as well as in the estimator.

Reference: docs/DECISIONS.md D-149, D-150, D-151, D-152, D-153, D-154.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from meridian.datasets.completeness import (
    POPULATIONS,
    STATUSES,
    USABLE_LABELS,
    StationDay,
    archive_station_days,
    match_receptions,
    own_eligible,
    own_station_days,
    summarise,
)
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.labels import LabelledPass
from meridian.datasets.propensity import BinnedPropensity, Candidate
from meridian.datasets.result import EvaluationResult, NotWeighted
from meridian.datasets.snapshot_rows import SnapshotRows
from meridian.datasets.weighting import Scored, weigh, weight_of

__all__ = [
    "NO_ELIGIBLE_PASSES",
    "Selection",
    "select",
]

NO_ELIGIBLE_PASSES = "no eligible passes to weight"


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What happened on a candidate's pass, kept apart from the candidate."""

    usable: bool
    success: bool


@dataclass(frozen=True, slots=True)
class Selection:
    """Everything a dataset says about how its passes were selected."""

    station_days: tuple[StationDay, ...]
    scored: tuple[Scored, ...]
    model: str
    floor: float
    unmatched_receptions: int
    """Archive receptions no computed pass could place (D-150)."""

    results: tuple[EvaluationResult, ...]
    """One per population, in :data:`POPULATIONS` order."""

    def propensity_rows(self) -> list[dict[str, object]]:
        """``propensities.jsonl``: each candidate, its cell and its weight.

        The weight is present for an attempted pass with support, and null
        otherwise; the outcome is not written here at all.
        """
        return [
            {
                "population": one.estimate.candidate.population,
                "station": one.estimate.candidate.station,
                "satellite_id": one.estimate.candidate.satellite_id,
                "aos": one.estimate.candidate.aos,
                "max_elevation_deg": one.estimate.candidate.max_elevation_deg,
                "attempted": one.estimate.candidate.attempted,
                "model": self.model,
                "level": one.estimate.level,
                "cell": list(one.estimate.cell),
                "cell_available": one.estimate.available,
                "cell_attempted": one.estimate.attempted,
                "propensity": one.estimate.propensity,
                "weight": (
                    weight_of(one.estimate, self.floor)
                    if one.estimate.candidate.attempted
                    else None
                ),
            }
            for one in self.scored
        ]

    def counts(self) -> dict[str, int]:
        """Station-days by population and status, every one present."""
        counts = {
            f"station_days.{population}.{status}": 0
            for population in POPULATIONS
            for status in STATUSES
        }
        for day in self.station_days:
            counts[f"station_days.{day.population}.{day.status}"] += 1
        return counts | {"archive_receptions.unmatched": self.unmatched_receptions}

    def summary(self) -> dict[str, object]:
        """What the manifest carries: every result, and what placed none."""
        return {
            "propensity_model": self.model,
            "unmatched_archive_receptions": self.unmatched_receptions,
            "populations": {one.population: one.parameters() for one in self.results},
        }


def select(
    labelled: Sequence[LabelledPass], rows: SnapshotRows, config: LabelConfig
) -> Selection:
    """Station-days, propensities and weights for both populations.

    Args:
        labelled: Every labelled physical pass, as :func:`label_passes` gave it.
        rows: The raw snapshot's rows, for peaks, longitudes and the archive.
        config: The labelling configuration.

    Returns:
        The selection, ready to be written.
    """
    archive_days, unmatched = archive_station_days(
        rows.archive_passes, rows.archive, config.completeness
    )
    days = own_station_days(labelled, config.completeness) + archive_days
    pairs = _own(labelled, rows) + _archive(rows, archive_days, config)
    model = BinnedPropensity(config.propensity)
    estimates = model.estimate([candidate for candidate, _ in pairs])
    scored = tuple(
        Scored(estimate=estimate, usable=outcome.usable, success=outcome.success)
        for estimate, (_, outcome) in zip(estimates, pairs, strict=True)
    )
    floor = config.propensity.floor
    results = tuple(
        EvaluationResult(
            population=population,
            completeness=summarise(days, population, config.completeness),
            weighting=(
                weigh(own, model=model.name, floor=floor)
                if (own := _of(scored, population))
                else NotWeighted(NO_ELIGIBLE_PASSES)
            ),
        )
        for population in POPULATIONS
    )
    return Selection(
        station_days=days,
        scored=scored,
        model=model.name,
        floor=floor,
        unmatched_receptions=unmatched,
        results=results,
    )


def _own(
    labelled: Sequence[LabelledPass], rows: SnapshotRows
) -> list[tuple[Candidate, _Outcome]]:
    """Our eligible passes, with the representative prediction's peak (D-148)."""
    peaks = {one.pass_id: one.max_elevation_deg for one in rows.passes}
    return [
        (
            Candidate(
                population="own",
                station=one.station_id,
                satellite_id=one.satellite_id,
                aos=one.aos,
                max_elevation_deg=peaks[one.pass_id],
                longitude_deg=rows.longitudes.get(one.station_id),
                attempted=one.source_outcome is not None,
            ),
            _Outcome(
                usable=one.label in USABLE_LABELS,
                success=one.label == "successful_reception",
            ),
        )
        for one in labelled
        if own_eligible(one)
    ]


def _archive(
    rows: SnapshotRows, days: Sequence[StationDay], config: LabelConfig
) -> list[tuple[Candidate, _Outcome]]:
    """The archive's computed passes on active days, at or above the floor."""
    settings = config.completeness
    active = {(day.station, day.day) for day in days if day.status != "inactive"}
    matches = match_receptions(
        rows.archive_passes, rows.archive, settings.archive_match_tolerance_s
    )
    pairs = []
    for one in rows.archive_passes:
        station = f"archive:{one.archive_station_id}"
        if (station, one.aos.date()) not in active or (
            one.max_elevation_deg < settings.archive_min_elevation_deg
        ):
            continue
        candidate = Candidate(
            population="archive",
            station=station,
            satellite_id=one.satellite_id,
            aos=one.aos,
            max_elevation_deg=one.max_elevation_deg,
            longitude_deg=rows.longitudes.get(station),
            attempted=one in matches.attempted,
        )
        outcome = _Outcome(
            usable=one in matches.usable, success=one in matches.succeeded
        )
        pairs.append((candidate, outcome))
    return pairs


def _of(scored: Sequence[Scored], population: str) -> list[Scored]:
    return [one for one in scored if one.estimate.candidate.population == population]

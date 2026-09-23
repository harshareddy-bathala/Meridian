"""Completeness per station-day — D-149 to D-151, as a pure function.

``EVALUATION.md`` §4.1 asks, for every station-day, what share of the passes
geometrically available to it the historical policy attempted. Two
populations, never pooled (D-053):

* **our stations**: one labelled physical pass each (D-148). Eligible if it is
  measured, its report window has settled and its satellite was not judged
  silent; attempted if some assignment of it received a report, or the
  registry confirmed the station was listening — a confirmed silence is an
  attempt that heard nothing (rule 7);
* **archive stations**: one computed pass each (D-150), eligible if it peaks at
  or above the configured floor; attempted if one of the station's receptions
  of that satellite starts inside its window, widened by the tolerance.

**A station-day is the UTC date of acquisition.** Its status is one of:

* ``retained`` — its completeness reaches the threshold;
* ``below_threshold`` — it does not;
* ``empty`` — it had no eligible pass, so no ratio;
* ``inactive`` — an archive station's day, inside its span of receptions, on
  which it received nothing. With no heartbeat we cannot tell a station that
  was off from one that chose nothing, and scoring the day 0 would claim the
  second (D-150).

``usable`` beside ``attempted`` counts the attempts whose outcome can be
scored — a yield label of ours, or an archive outcome other than ``unknown``.
It is reported, never folded into the ratio (D-149).

**Ratios are compared as integers.** A histogram bin is ``attempted * 10 //
eligible``, so a day at exactly 0.7 is in the 0.7 bin rather than wherever
floating-point division happens to put it.

Reference: docs/DECISIONS.md D-148, D-149, D-150, D-151.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from meridian.datasets.archive_matching import ReceptionMatches
from meridian.datasets.label_config import CompletenessConfig
from meridian.datasets.labels import LabelledPass
from meridian.datasets.snapshot_rows import ArchivePassRow

__all__ = [
    "POPULATIONS",
    "STATUSES",
    "USABLE_LABELS",
    "CompletenessSummary",
    "Distribution",
    "StationDay",
    "archive_station_days",
    "own_attempted",
    "own_eligible",
    "own_station_days",
    "reaches",
    "summarise",
]

POPULATIONS = ("own", "archive")
STATUSES = ("retained", "below_threshold", "empty", "inactive")

USABLE_LABELS = frozenset(
    ("successful_reception", "signal_no_decode", "confirmed_miss")
)
_NOT_ELIGIBLE = frozenset(("report_window_open", "simulated"))
_HEARD_NOTHING = frozenset(
    ("confirmed_miss", "satellite_silent", "satellite_state_indeterminate")
)
"""Labels only a confirmed-listening silence reaches (D-146 rule 9): an attempt,
reported or not."""
_BINS = 10


@dataclass(frozen=True, slots=True)
class StationDay:
    """One row of ``station_days.jsonl``."""

    population: str
    station: str
    """Our ``station_id``, or ``archive:<archive_station_id>``."""

    day: date
    eligible: int
    attempted: int
    usable: int
    status: str

    @property
    def completeness(self) -> float | None:
        """Attempted over eligible, or None for a day with nothing eligible."""
        if self.status in ("empty", "inactive"):
            return None
        return self.attempted / self.eligible

    def row(self) -> dict[str, object]:
        """The row as it is written."""
        return {
            "population": self.population,
            "station": self.station,
            "day": self.day.isoformat(),
            "eligible": self.eligible,
            "attempted": self.attempted,
            "usable": self.usable,
            "completeness": self.completeness,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Distribution:
    """How completeness is spread over the station-days that have a ratio."""

    count: int
    deciles: tuple[float, ...]
    """The 10th to 90th percentiles by nearest rank; empty when ``count`` is 0."""

    histogram: tuple[int, ...]
    """Ten bins of width 0.1; the last holds 1.0 as well."""


@dataclass(frozen=True, slots=True)
class CompletenessSummary:
    """One population's completeness, as every report must state it (D-151)."""

    population: str
    threshold: float
    statuses: dict[str, int]
    """Station-days by status, every status present."""

    eligible: int
    """Over the days with a ratio: an ``inactive`` day's passes are not the
    station's to have declined (D-150), so they are in no total."""

    attempted: int
    usable: int
    distribution: Distribution
    sensitivity: tuple[tuple[float, int, int], ...]
    """``(threshold, retained, excluded)`` for each sensitivity threshold."""

    def parameters(self) -> dict[str, object]:
        """The summary as plain values, for a manifest or a report."""
        return {
            "threshold": self.threshold,
            "station_days": dict(self.statuses),
            "eligible": self.eligible,
            "attempted": self.attempted,
            "usable": self.usable,
            "distribution": {
                "count": self.distribution.count,
                "deciles": list(self.distribution.deciles),
                "histogram": list(self.distribution.histogram),
            },
            "sensitivity": [
                {"threshold": t, "retained": kept, "excluded": dropped}
                for t, kept, dropped in self.sensitivity
            ],
        }


@dataclass(frozen=True, slots=True)
class _Tally:
    eligible: int = 0
    attempted: int = 0
    usable: int = 0


def own_station_days(
    labelled: Iterable[LabelledPass], config: CompletenessConfig
) -> tuple[StationDay, ...]:
    """Our stations' days, from their labelled physical passes.

    Simulated passes are left out entirely: a simulated station has no
    station-days in a measured ratio, and no ratio is computed over both.
    """
    tallies: dict[tuple[str, date], _Tally] = {}
    for one in labelled:
        if one.simulated:
            continue
        key = (one.station_id, one.aos.date())
        held = tallies.get(key, _Tally())
        if own_eligible(one):
            held = _Tally(
                eligible=held.eligible + 1,
                attempted=held.attempted + own_attempted(one),
                usable=held.usable + (one.label in USABLE_LABELS),
            )
        tallies[key] = held
    return tuple(
        _day("own", station, day, tally, _status(tally, config.threshold))
        for (station, day), tally in sorted(tallies.items())
    )


def archive_station_days(
    passes: Sequence[ArchivePassRow],
    matches: ReceptionMatches,
    config: CompletenessConfig,
) -> tuple[StationDay, ...]:
    """Archive stations' days, from their computed passes and their receptions.

    Only a station with computed passes has days: one without was not placed
    at export, and the manifest already counts why (D-150).

    Args:
        passes: The archive's computed passes.
        matches: Where :func:`match_receptions` placed the receptions.
        config: The completeness settings.
    """
    tallies: dict[tuple[int, date], _Tally] = {
        (station, day): _Tally()
        for station, days in matches.heard.items()
        for day in _span(min(days), max(days))
    }
    for candidate in passes:
        key = (candidate.archive_station_id, candidate.aos.date())
        if (
            key in tallies
            and candidate.max_elevation_deg >= config.archive_min_elevation_deg
        ):
            held = tallies[key]
            tallies[key] = _Tally(
                eligible=held.eligible + 1,
                attempted=held.attempted + (candidate in matches.attempted),
                usable=held.usable + (candidate in matches.usable),
            )
    return tuple(
        _day(
            "archive",
            f"archive:{station}",
            day,
            tally,
            _status(
                tally,
                config.threshold,
                active=day in matches.heard[station],
            ),
        )
        for (station, day), tally in sorted(tallies.items())
    )


def summarise(
    days: Sequence[StationDay], population: str, config: CompletenessConfig
) -> CompletenessSummary:
    """One population's summary: statuses, distribution, sensitivity (D-151)."""
    own = [one for one in days if one.population == population]
    rated = [one for one in own if one.completeness is not None]
    statuses = dict.fromkeys(STATUSES, 0)
    for one in own:
        statuses[one.status] += 1
    return CompletenessSummary(
        population=population,
        threshold=config.threshold,
        statuses=statuses,
        eligible=sum(one.eligible for one in rated),
        attempted=sum(one.attempted for one in rated),
        usable=sum(one.usable for one in rated),
        distribution=_distribution(rated),
        sensitivity=tuple(
            (
                threshold,
                kept := sum(
                    1
                    for one in rated
                    if reaches(one.attempted, one.eligible, threshold)
                ),
                len(rated) - kept,
            )
            for threshold in config.sensitivity
        ),
    )


def own_attempted(one: LabelledPass) -> bool:
    """A report arrived, or the registry confirmed the station listened (rule 7)."""
    return one.source_outcome is not None or one.label in _HEARD_NOTHING


def reaches(attempted: int, eligible: int, threshold: float) -> bool:
    """Whether a day this complete is retained: the one comparison, everywhere."""
    return attempted / eligible >= threshold


def own_eligible(one: LabelledPass) -> bool:
    """Measured, settled, and not a silent satellite (D-149)."""
    if one.simulated:
        return False
    return one.exclusion_reason not in _NOT_ELIGIBLE and one.label != "satellite_silent"


def _status(tally: _Tally, threshold: float, *, active: bool = True) -> str:
    """Which of :data:`STATUSES` a day with this tally has."""
    if not active:
        return "inactive"
    if tally.eligible == 0:
        return "empty"
    if reaches(tally.attempted, tally.eligible, threshold):
        return "retained"
    return "below_threshold"


def _day(
    population: str, station: str, day: date, tally: _Tally, status: str
) -> StationDay:
    return StationDay(
        population=population,
        station=station,
        day=day,
        eligible=tally.eligible,
        attempted=tally.attempted,
        usable=tally.usable,
        status=status,
    )


def _span(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _distribution(rated: Sequence[StationDay]) -> Distribution:
    values = sorted(one.attempted / one.eligible for one in rated)
    histogram = [0] * _BINS
    for one in rated:
        histogram[min(one.attempted * _BINS // one.eligible, _BINS - 1)] += 1
    deciles = (
        tuple(values[ceil(q * len(values) / _BINS) - 1] for q in range(1, _BINS))
        if values
        else ()
    )
    return Distribution(count=len(values), deciles=deciles, histogram=tuple(histogram))

"""Completeness per station-day — D-149 to D-151, as a pure function.

``EVALUATION.md`` §4.1 asks, for every station-day, what share of the passes
geometrically available to it the historical policy attempted. Two
populations, never pooled (D-053):

* **our stations**: one labelled physical pass each (D-148). Eligible if it is
  measured, its report window has settled and its satellite was not judged
  silent; attempted if some assignment of it received a report;
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

from meridian.datasets.label_config import CompletenessConfig
from meridian.datasets.labels import LabelledPass
from meridian.datasets.snapshot_rows import ArchivePassRow, ArchiveReception

__all__ = [
    "POPULATIONS",
    "STATUSES",
    "USABLE_LABELS",
    "CompletenessSummary",
    "Distribution",
    "ReceptionMatches",
    "StationDay",
    "archive_station_days",
    "match_receptions",
    "own_eligible",
    "own_station_days",
    "summarise",
]

POPULATIONS = ("own", "archive")
STATUSES = ("retained", "below_threshold", "empty", "inactive")

USABLE_LABELS = frozenset(
    ("successful_reception", "signal_no_decode", "confirmed_miss")
)
_NOT_ELIGIBLE = frozenset(("report_window_open", "simulated"))
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
                attempted=held.attempted + (one.source_outcome is not None),
                usable=held.usable + (one.label in USABLE_LABELS),
            )
        tallies[key] = held
    return tuple(
        _day("own", station, day, tally, _status(tally, config.threshold))
        for (station, day), tally in sorted(tallies.items())
    )


def archive_station_days(
    passes: Sequence[ArchivePassRow],
    receptions: Iterable[ArchiveReception],
    config: CompletenessConfig,
) -> tuple[tuple[StationDay, ...], int]:
    """Archive stations' days, from their computed passes and their receptions.

    Only a station with computed passes has days: one without was not placed
    at export, and the manifest already counts why (D-150).

    Returns:
        The station-days, and how many receptions matched no computed pass —
        each one a reception the denominator could not place.
    """
    matches = match_receptions(passes, receptions, config.archive_match_tolerance_s)
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
    rows = tuple(
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
    return rows, matches.unmatched


@dataclass(frozen=True, slots=True)
class ReceptionMatches:
    """Which passes the receptions claim, and the days each station was heard."""

    heard: dict[int, set[date]]
    attempted: frozenset[ArchivePassRow]
    usable: frozenset[ArchivePassRow]
    succeeded: frozenset[ArchivePassRow]
    """Matched by a ``decoded`` reception: an archive success (D-153)."""

    unmatched: int


def match_receptions(
    passes: Sequence[ArchivePassRow],
    receptions: Iterable[ArchiveReception],
    tolerance_s: int,
) -> ReceptionMatches:
    """Place each reception on the computed pass it belongs to, if any."""
    by_pair: dict[tuple[int, str], list[ArchivePassRow]] = {}
    for candidate in passes:
        pair = (candidate.archive_station_id, candidate.satellite_id)
        by_pair.setdefault(pair, []).append(candidate)
    placed = {candidate.archive_station_id for candidate in passes}
    heard: dict[int, set[date]] = {}
    attempted: set[ArchivePassRow] = set()
    usable: set[ArchivePassRow] = set()
    succeeded: set[ArchivePassRow] = set()
    unmatched = 0
    for reception in receptions:
        match = _matching_pass(reception, by_pair, tolerance_s)
        if reception.archive_station_id in placed:
            heard.setdefault(reception.archive_station_id, set()).add(
                reception.started_at.date()
            )
        if match is None:
            unmatched += 1
            continue
        attempted.add(match)
        if reception.archive_outcome != "unknown":
            usable.add(match)
        if reception.archive_outcome == "decoded":
            succeeded.add(match)
    return ReceptionMatches(
        heard=heard,
        attempted=frozenset(attempted),
        usable=frozenset(usable),
        succeeded=frozenset(succeeded),
        unmatched=unmatched,
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
                kept := sum(1 for one in rated if _reaches(one, threshold)),
                len(rated) - kept,
            )
            for threshold in config.sensitivity
        ),
    )


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
    if tally.attempted / tally.eligible >= threshold:
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


def _matching_pass(
    reception: ArchiveReception,
    by_pair: dict[tuple[int, str], list[ArchivePassRow]],
    tolerance_s: int,
) -> ArchivePassRow | None:
    """The computed pass a reception belongs to: nearest acquisition wins."""
    if reception.satellite_key_kind != "norad":
        return None
    widen = timedelta(seconds=tolerance_s)
    candidates = [
        one
        for one in by_pair.get(
            (reception.archive_station_id, reception.satellite_key), []
        )
        if one.aos - widen <= reception.started_at < one.los + widen
    ]
    return min(
        candidates,
        key=lambda one: (abs(one.aos - reception.started_at), one.aos),
        default=None,
    )


def _span(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _reaches(day: StationDay, threshold: float) -> bool:
    return day.attempted / day.eligible >= threshold


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

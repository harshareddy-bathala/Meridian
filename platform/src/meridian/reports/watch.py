"""The receive-chain watch section: SC-9, from sealed simulated fleets.

*The health watch warns before reception fails: a warning before the first
failed reception in at least 90% of injected degradation runs, and at most one
false alarm per station per 30 days on fault-free runs* (``EVALUATION.md``
§11.3) — proposed, to agree with the team.

Every station of every sealed run (``meridian.datasets.watch_runs``) is judged
from the run's files alone:

* **a degradation** is the ledger's ``signal_degradation`` window on that
  station: its onset, and its rate in decibels a day;
* **a failure** is a scheduled pass, begun at or after the onset, that the
  outcome model decodes with nothing wrong and that did not decode: a capture
  the degradation took away (D-296);
* **the warning** is the station's first receive-chain warning raised at or
  after the onset;

and the section reports, per degradation:

* **delay**, from onset to warning, and **the loss in dB** the chain had
  accrued by then, from the ledger's rate;
* **lead**, from the warning to the first failure; positive is a warning that
  came first;
* **which came first**: the warning, the failure, or, by the run's end,
  neither. *Neither* is reported and left out of SC-9's share, because a run
  that ended before either says nothing about their order;

and, from fault-free fleets, **false alarms**: every warning their stations
raised after the clean stretch, per station per 30 days watched, with an exact
Poisson interval.

**Every row is simulated** (rule 5), and SC-9 is stated with the narrow claim
beside it (D-105): a simulated degradation's shortfall is detected, not a real
chain's.

Reference: docs/DECISIONS.md D-105, D-253, D-292, D-294, D-296;
``EVALUATION.md`` §11.3.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median

from meridian.datasets.manifest import content_sha256
from meridian.datasets.watch_runs import WatchRun
from meridian.datasets.weighting import wilson

__all__ = [
    "FALSE_ALARM_TARGET",
    "WARNED_FIRST_TARGET",
    "WATCH_FILE",
    "Degradation",
    "judge_degradations",
    "watch_rows",
]

WATCH_FILE = "watch.jsonl"

WARNED_FIRST_TARGET = 0.90
FALSE_ALARM_TARGET = 1.0
"""SC-9's proposed targets: the share warned first, and false alarms per
station per 30 days (``EVALUATION.md`` §1)."""

DEGRADATION = "signal_degradation"
_MONTH_DAYS = 30.0

Rows = list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class Degradation:
    """One station's injected degradation, judged."""

    run: str
    station_index: int
    rate_db_per_day: float
    onset: datetime
    warned_at: datetime | None
    failed_at: datetime | None
    """When the first pass the degradation cost began."""

    first: str
    """``warning``, ``failure`` or ``neither``, by the run's end."""

    @property
    def delay_h(self) -> float | None:
        """Hours from onset to warning, or ``None`` with no warning."""
        return _hours(self.warned_at - self.onset) if self.warned_at else None

    @property
    def loss_db(self) -> float | None:
        """The loss accrued by the warning, from the ledger's rate."""
        if self.warned_at is None:
            return None
        days = (self.warned_at - self.onset).total_seconds() / 86_400
        return round(days * self.rate_db_per_day, 2)

    @property
    def lead_h(self) -> float | None:
        """Hours from warning to first failure; positive when warned first."""
        if self.warned_at is None or self.failed_at is None:
            return None
        return _hours(self.failed_at - self.warned_at)


def _hours(span: timedelta) -> float:
    return round(span.total_seconds() / 3600, 2)


def _at(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _name(run: WatchRun) -> str:
    return content_sha256(run.directory.manifest).hex()[:12]


def judge_degradations(run: WatchRun) -> list[Degradation]:
    """Every degradation the run's ledger opened, against what followed it."""
    indexes = {
        str(station): number
        for number, station in enumerate(_listed(run.run.get("station_ids")), start=1)
    }
    found = []
    for fault in run.faults:
        if fault.kind != DEGRADATION or fault.station_id not in indexes:
            continue
        index = indexes[fault.station_id]
        rate = float(str(dict(fault.detail).get("rate_db_per_day", 0.0)))
        warned = min(
            (
                _at(one["raised_at"])
                for one in run.warnings
                if one["station_index"] == index
                and _at(one["raised_at"]) >= fault.opened_at
            ),
            default=None,
        )
        failed = min(
            (
                _at(one["start_at"])
                for one in run.cases
                if one["station_index"] == index
                and _at(one["start_at"]) >= fault.opened_at
                and one["clean_outcome"] == "decoded"
                and one["outcome"] != "decoded"
            ),
            default=None,
        )
        found.append(
            Degradation(
                run=_name(run),
                station_index=index,
                rate_db_per_day=rate,
                onset=fault.opened_at,
                warned_at=warned,
                failed_at=failed,
                first=_first(warned, failed),
            )
        )
    return found


def _first(warned: datetime | None, failed: datetime | None) -> str:
    if warned is None and failed is None:
        return "neither"
    if failed is None or (warned is not None and warned < failed):
        return "warning"
    return "failure"


def _listed(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def watch_rows(runs: Sequence[WatchRun]) -> Rows:
    """Every row of the section, from the sealed runs alone."""
    sealed = {_name(run): run for run in runs}
    degraded = [
        one
        for run in sealed.values()
        if run.run.get("scenario") == "degradation"
        for one in judge_degradations(run)
    ]
    clean = [run for run in sealed.values() if run.run.get("scenario") == "clean"]
    rows: Rows = [
        {
            "row": "runs",
            "simulated": True,
            "runs": [{"run": name, **_described(run)} for name, run in sealed.items()],
        }
    ]
    rows.extend(_degradation_row(one) for one in degraded)
    rows.append(_summary(degraded))
    rows.append(_false_alarms(clean))
    rows.append(_sc9(rows, measured=bool(degraded) or bool(clean)))
    return rows


def _described(run: WatchRun) -> dict[str, object]:
    keep = ("scenario", "master_seed", "stations", "start", "watched_from", "end")
    return {key: run.run.get(key) for key in keep}


def _degradation_row(one: Degradation) -> dict[str, object]:
    return {
        "row": "degradation",
        "run": one.run,
        "station_index": one.station_index,
        "rate_db_per_day": one.rate_db_per_day,
        "onset": one.onset.isoformat(),
        "warned_at": None if one.warned_at is None else one.warned_at.isoformat(),
        "failed_at": None if one.failed_at is None else one.failed_at.isoformat(),
        "delay_h": one.delay_h,
        "loss_db": one.loss_db,
        "lead_h": one.lead_h,
        "first": one.first,
        "simulated": True,
    }


def _spread(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "min": min(values, default=None),
        "median": median(values) if values else None,
        "max": max(values, default=None),
    }


def _summary(degraded: Sequence[Degradation]) -> dict[str, object]:
    decided = [one for one in degraded if one.first != "neither"]
    warned_first = sum(one.first == "warning" for one in decided)
    return {
        "row": "summary",
        "degradations": len(degraded),
        "decided": len(decided),
        "neither": len(degraded) - len(decided),
        "warned_first": warned_first,
        "warned_first_share": (
            asdict(wilson(warned_first / len(decided), len(decided)))
            if decided
            else None
        ),
        "delay_h": _spread(
            [one.delay_h for one in degraded if one.delay_h is not None]
        ),
        "loss_db": _spread(
            [one.loss_db for one in degraded if one.loss_db is not None]
        ),
        "lead_h": _spread([one.lead_h for one in degraded if one.lead_h is not None]),
        "simulated": True,
    }


def _false_alarms(clean: Sequence[WatchRun]) -> dict[str, object]:
    station_days = 0.0
    alarms = 0
    for run in clean:
        watched_from = _at(run.run["watched_from"])
        days = (_at(run.run["end"]) - watched_from).total_seconds() / 86_400
        station_days += days * int(str(run.run["stations"]))
        alarms += sum(_at(one["raised_at"]) >= watched_from for one in run.warnings)
    months = station_days / _MONTH_DAYS
    low, high = _poisson_interval(alarms)
    return {
        "row": "false_alarms",
        "runs": len(clean),
        "station_days": round(station_days, 2),
        "warnings": alarms,
        "per_station_30_days": round(alarms / months, 3) if months else None,
        "interval": (
            {"low": round(low / months, 3), "high": round(high / months, 3)}
            if months
            else None
        ),
        "simulated": True,
    }


def _poisson_interval(count: int) -> tuple[float, float]:
    """An exact 95% interval for a Poisson count, from its chi-squared bounds.

    Computed by bisection on the Poisson tail, so no statistics library is
    needed: the lower bound has P(X ≥ count) = 0.025, the upper P(X ≤ count) =
    0.025.
    """

    def tail_at_most(mean: float, k: int) -> float:
        term = total = math.exp(-mean)
        for n in range(1, k + 1):
            term *= mean / n
            total += term
        return total

    def solve(target: float, k: int) -> float:
        low, high = 0.0, max(10.0, 10.0 * (k + 1))
        for _ in range(200):
            mid = (low + high) / 2
            if tail_at_most(mid, k) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    lower = 0.0 if count == 0 else solve(0.975, count - 1)
    return lower, solve(0.025, count)


def _sc9(rows: Rows, *, measured: bool) -> dict[str, object]:
    """SC-9 against its proposed targets."""
    if not measured:
        return {"row": "sc9", "status": "not_measured", "simulated": True}
    (summary,) = (one for one in rows if one["row"] == "summary")
    (alarms,) = (one for one in rows if one["row"] == "false_alarms")
    decided = int(str(summary["decided"]))
    share = int(str(summary["warned_first"])) / decided if decided else None
    rate = alarms["per_station_30_days"]
    warned_met = share is not None and share >= WARNED_FIRST_TARGET
    alarms_met = isinstance(rate, float) and rate <= FALSE_ALARM_TARGET
    return {
        "row": "sc9",
        "status": "measured",
        "warned_first_target": WARNED_FIRST_TARGET,
        "false_alarm_target": FALSE_ALARM_TARGET,
        "warned_first": share,
        "false_alarms": rate,
        "warned_first_met": warned_met,
        "false_alarms_met": alarms_met,
        "met": warned_met and alarms_met,
        "simulated": True,
    }

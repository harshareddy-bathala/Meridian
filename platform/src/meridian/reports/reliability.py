"""The reliability section's snapshot half: indicators, SC-4, and the budget history.

Counted from the evaluation dataset's labels by the functions ``meridian
snapshot reliability`` calls (D-184, D-185), so the report and that command
cannot disagree:

* **every indicator over the window** ending at the snapshot's ``as_of`` —
  capture, confirmed misses, assignment completion, schedule execution — each
  with its Wilson interval, per population and per station, judged against its
  target by :func:`~meridian.reliability.report.slo_results`;
* **SC-4** — measured capture over the window, against 90%, stated as the
  point and the whole interval like every other claim;
* **the loss budget's history** — the same window ending at ``as_of`` and every
  ``history_step_days`` before it, back to the snapshot's start, so a reader
  sees how the budget was spent, not only where it stands. A window reaching
  before the snapshot's ``since`` is marked **partial**: its early passes are
  not in the snapshot, and it is never read as a whole window.

Station availability and submission delay are not in a snapshot (D-184), and
the section says so rather than leaving a blank.

Reference: docs/DECISIONS.md D-184, D-185, D-240; ``EVALUATION.md`` §1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from meridian.datasets.label_rows import read_labels
from meridian.datasets.labels import LabelledPass
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.reliability_rows import (
    NO_AVAILABILITY,
    NO_SUBMISSION_DELAY,
    passes_in_window,
)
from meridian.reliability.budget import LossBudget, loss_budget
from meridian.reliability.report import (
    NotMeasured,
    PopulationReport,
    build_report,
    slo_results,
)
from meridian.reliability.slis import Proportion, capture_rate
from meridian.reports.config import ReliabilitySectionConfig

__all__ = ["RELIABILITY_FILE", "SC4_WINDOW_DAYS", "snapshot_rows"]

RELIABILITY_FILE = "reliability.jsonl"

SC4_WINDOW_DAYS = 30
"""SC-4 is a 30-day figure; a report over another window says so."""

_PLACES = 6

Row = dict[str, object]


def snapshot_rows(
    dataset: SnapshotDirectory, config: ReliabilitySectionConfig
) -> list[Row]:
    """The window's indicators, every target, SC-4, and the budget's history."""
    labelled = read_labels(dataset.files)
    manifest = dataset.manifest
    window = config.slo.window_days
    report = build_report(
        passes_in_window(labelled, as_of=manifest.as_of, window_days=window),
        header=(
            f"snapshot {content_sha256(manifest).hex()}",
            (manifest.as_of - timedelta(days=window), manifest.as_of),
            manifest.transformation_version or "unversioned",
            (manifest.config_sha256 or b"").hex(),
        ),
        slo=config.slo,
        availability=NotMeasured(NO_AVAILABILITY),
        submission_delays=NotMeasured(NO_SUBMISSION_DELAY),
    )
    rows: list[Row] = [
        {
            "row": "window",
            "start": report.window[0],
            "end": report.window[1],
            "days": window,
            "not_in_a_snapshot": {
                "availability": NO_AVAILABILITY,
                "submission_delay": NO_SUBMISSION_DELAY,
            },
        }
    ]
    for population in (report.measured, report.simulated):
        rows.extend(_population(population, config))
    rows.extend(_history(labelled, manifest.since, manifest.as_of, config))
    rows.append(_sc4(report.measured, config))
    return rows


def _population(
    population: PopulationReport, config: ReliabilitySectionConfig
) -> list[Row]:
    name = "simulated" if population.simulated else "measured"
    rows: list[Row] = [
        {
            "row": "indicators",
            "population": name,
            "passes": population.passes,
            "capture": _proportion(population.capture),
            "confirmed_miss": _proportion(population.confirmed_miss),
            "completion": _proportion(population.completion),
            "execution": _proportion(population.execution),
            "budget": _budget(population.budget),
        }
    ]
    rows.extend(
        {
            "row": "target",
            "population": name,
            "name": one.name,
            "target": one.target,
            "at_least": one.at_least,
            "value": None if one.value is None else _real(one.value),
            "met": one.met,
            "claim": one.claim,
        }
        for one in slo_results(population, config.slo)
    )
    rows.extend(
        {
            "row": "station",
            "population": name,
            "station_id": one.station_id,
            "capture": _proportion(one.capture),
            "budget": _budget(one.budget),
        }
        for one in population.stations
    )
    return rows


def _history(
    labelled: Sequence[LabelledPass],
    since: datetime,
    as_of: datetime,
    config: ReliabilitySectionConfig,
) -> list[Row]:
    """The window ending at ``as_of`` and every step before it, back to ``since``."""
    window, step = config.slo.window_days, timedelta(days=config.history_step_days)
    ends = []
    end = as_of
    while end > since:
        ends.append(end)
        end -= step
    rows: list[Row] = []
    for end in reversed(ends):
        passes = passes_in_window(labelled, as_of=end, window_days=window)
        for simulated in (False, True):
            mine = [one for one in passes if one.simulated == simulated]
            rows.append(
                {
                    "row": "history",
                    "population": "simulated" if simulated else "measured",
                    "end": end,
                    "partial": end - timedelta(days=window) < since,
                    "capture": _proportion(capture_rate(mine)),
                    "budget": _budget(
                        loss_budget(mine, capture_target=config.slo.capture_rate_min)
                    ),
                }
            )
    return rows


def _sc4(measured: PopulationReport, config: ReliabilitySectionConfig) -> Row:
    """SC-4 from measured capture over the window: the point, and its interval."""
    capture = _proportion(measured.capture)
    target = config.slo.capture_rate_min
    row: Row = {
        "row": "sc4",
        "target": target,
        "window_days": config.slo.window_days,
        "sc4_window": config.slo.window_days == SC4_WINDOW_DAYS,
    }
    estimate = capture["estimate"]
    if not isinstance(estimate, float):
        return row | {"status": "not measured", "reason": "no measured pass settled"}
    interval = capture["interval"]
    return row | {
        "status": "measured",
        "capture": estimate,
        "interval": interval,
        "n": capture["denominator"],
        "point_meets": estimate >= target,
        "interval_above": isinstance(interval, dict) and interval["low"] >= target,
    }


def _proportion(value: Proportion) -> Row:
    interval = value.interval
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "estimate": None if value.estimate is None else _real(value.estimate),
        "interval": None
        if interval is None
        else {"low": _real(interval[0]), "high": _real(interval[1])},
    }


def _budget(value: LossBudget) -> Row:
    ratio = value.remaining_ratio
    return {
        "target": value.capture_target,
        "eligible": value.eligible,
        "allowed": _real(value.allowed),
        "spent": value.spent,
        "remaining": _real(value.remaining),
        "remaining_ratio": None if ratio is None else _real(ratio),
        "exhausted": value.exhausted,
        "by_reason": value.by_reason(),
    }


def _real(value: float) -> float:
    return round(value, _PLACES)

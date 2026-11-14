"""A sealed run's success criteria, summarised for the dashboard to publish.

``meridian report criteria <run> --output <file>`` writes ``criteria-summary-1``:
the run's hash and method, and for each criterion the run judges (SC-1 to SC-5
and SC-7 to SC-9) whether it was measured, on whose passes, its figures with
their intervals and targets, and whether the target was met.

**Copied, never recomputed.** Each section writes its criterion as a row of its
own shape: SC-1's relative gain, SC-5's slowest detection, SC-8's recall per
cause. This module states each one the same way, ``value``, ``low``, ``high``,
``target`` and which side of it counts, by copying the section's own fields.
Whether a target was met is the section's own judgement, never this module's.
Every file is read through :func:`~meridian.datasets.publish.read_directory`,
which refuses a run whose files no longer match its manifest (D-325).

**Whose passes.** SC-1 to SC-4 and SC-7 are measured on our own measured
stations: the replay, the fits and the reliability section leave simulated
ones out. SC-5, SC-8 and SC-9 are measured on simulated fault runs, and say so
(rule 5).

**A criterion the run could not judge says why**, in the section's own words,
or that the run was given nothing to judge it from. It carries no figure at
all, never a zero (D-086).

**Met is not always claimable.** SC-4's target is over thirty days, and a
shorter window may meet it without claiming it; SC-8 is not claimed until the
injected faults' effects are reviewed (D-278). Both are stated.

SC-6 and SC-10 are not judged by an evaluation run. ``docs/ACCEPTANCE.md``
holds their evidence.

Reference: docs/DECISIONS.md D-086, D-234, D-278, D-325, D-368.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.reports.diagnosis import DIAGNOSIS_FILE
from meridian.reports.orbit import ORBIT_FILE
from meridian.reports.prediction_rows import PREDICTION_FILE
from meridian.reports.reliability import RELIABILITY_FILE
from meridian.reports.scheduling import SCHEDULING_FILE
from meridian.reports.summary import NotSummarisableError
from meridian.reports.verdict import VERDICT_FILE
from meridian.reports.watch import WATCH_FILE

__all__ = ["CRITERIA", "PROPOSED", "SCHEMA", "criteria_summary"]

SCHEMA = "criteria-summary-1"

PROPOSED = frozenset({"SC-7", "SC-8", "SC-9"})
"""Criteria whose targets are proposed, to agree with the team
(``EVALUATION.md`` §1)."""

Row = Mapping[str, object]
Rows = Sequence[Row]

_RUN_FILE = "run.jsonl"


@dataclass(frozen=True, slots=True)
class _Criterion:
    """Where a criterion's row is, and how its figures are read from it."""

    file: str
    row: str
    simulated: bool
    figures: Callable[[Row, Rows], list[dict[str, object]]]
    absent: str
    """Why the row is missing: what the run was not given."""


def _figure(  # noqa: PLR0913 — one figure's fields, the last three by name
    name: str,
    value: object,
    interval: object,
    *,
    target: object,
    n: object,
    at_most: bool = False,
) -> dict[str, object]:
    bounds = interval if isinstance(interval, Mapping) else {}
    return {
        "name": name,
        "value": _real(value),
        "low": _maybe_real(bounds.get("low")),
        "high": _maybe_real(bounds.get("high")),
        "target": _real(target),
        "direction": "at_most" if at_most else "at_least",
        "n": _maybe_count(n),
    }


def _one(
    name: str, value: str, n: str
) -> Callable[[Row, Rows], list[dict[str, object]]]:
    """A criterion whose row is one estimate, its interval, a target and a count."""

    def read(row: Row, _: Rows) -> list[dict[str, object]]:
        return [
            _figure(name, row[value], row["interval"], target=row["target"], n=row[n])
        ]

    return read


def _sc5(row: Row, _: Rows) -> list[dict[str, object]]:
    return [
        _figure(
            "slowest_detection_s",
            row["max_s"],
            None,
            target=row["target_s"],
            n=row["n"],
            at_most=True,
        )
    ]


def _sc8(row: Row, rows: Rows) -> list[dict[str, object]]:
    figures = [
        _figure(
            f"recall_{one['cause']}",
            recall["estimate"],
            recall,
            target=row["recall_target"],
            n=one["cases"],
        )
        for one in rows
        if one.get("row") == "recall"
        and isinstance(recall := one.get("recall"), Mapping)
    ]
    (fractions,) = (one for one in rows if one.get("row") == "fractions")
    if fractions["wrong_fraction"] is not None:
        figures.append(
            _figure(
                "wrong_fraction",
                fractions["wrong_fraction"],
                None,
                target=row["wrong_target"],
                n=fractions["diagnoses"],
                at_most=True,
            )
        )
    return figures


def _sc9(row: Row, rows: Rows) -> list[dict[str, object]]:
    (summary,) = (one for one in rows if one.get("row") == "summary")
    (alarms,) = (one for one in rows if one.get("row") == "false_alarms")
    figures = []
    share = summary["warned_first_share"]
    if isinstance(share, Mapping):
        figures.append(
            _figure(
                "warned_first",
                share["estimate"],
                share,
                target=row["warned_first_target"],
                n=summary["decided"],
            )
        )
    if alarms["per_station_30_days"] is not None:
        figures.append(
            _figure(
                "false_alarms_per_station_30_days",
                alarms["per_station_30_days"],
                alarms["interval"],
                target=row["false_alarm_target"],
                n=None,
                at_most=True,
            )
        )
    return figures


CRITERIA: Mapping[str, _Criterion] = {
    "SC-1": _Criterion(
        SCHEDULING_FILE,
        "sc1",
        False,
        _one("relative_gain", "relative", "station_days"),
        "the run judged no schedule",
    ),
    "SC-2": _Criterion(
        PREDICTION_FILE,
        "sc2",
        False,
        _one("skill", "skill", "n"),
        "the run fitted no model",
    ),
    "SC-3": _Criterion(
        ORBIT_FILE,
        "sc3",
        False,
        _one("coverage", "coverage", "n"),
        "the run read no detection",
    ),
    "SC-4": _Criterion(
        RELIABILITY_FILE,
        "sc4",
        False,
        _one("capture_rate", "capture", "n"),
        "the run read no settled pass",
    ),
    "SC-5": _Criterion(
        RELIABILITY_FILE, "sc5", True, _sc5, "the run was given no fault run"
    ),
    "SC-7": _Criterion(
        VERDICT_FILE,
        "sc7",
        False,
        _one("skill", "skill", "n"),
        "the run judged no reception verdict",
    ),
    "SC-8": _Criterion(
        DIAGNOSIS_FILE, "sc8", True, _sc8, "the run was given no diagnosis run"
    ),
    "SC-9": _Criterion(
        WATCH_FILE, "sc9", True, _sc9, "the run was given no health-watch run"
    ),
}
"""Every criterion an evaluation run judges, in order."""


def criteria_summary(run: SnapshotDirectory) -> dict[str, object]:
    """The criteria of one sealed evaluation report.

    Raises:
        NotSummarisableError: The directory is not an evaluation report, or a
            criterion's row is not the shape its section writes.
    """
    if run.manifest.kind != "evaluation_report":
        message = f"{run.path} is a {run.manifest.kind}, not an evaluation report"
        raise NotSummarisableError(message)
    (record,) = (one for one in _rows(run, _RUN_FILE) if one.get("row") == "run")
    try:
        criteria = [_criterion(run, name, how) for name, how in CRITERIA.items()]
    except (KeyError, TypeError, ValueError) as exc:
        message = f"{run.path} holds a criterion this summary cannot read: {exc}"
        raise NotSummarisableError(message) from exc
    return {
        "schema": SCHEMA,
        "run_sha256": content_sha256(run.manifest).hex(),
        "method": str(record["method"]),
        "since": run.manifest.since.isoformat(),
        "as_of": run.manifest.as_of.isoformat(),
        "criteria": criteria,
    }


def _criterion(run: SnapshotDirectory, name: str, how: _Criterion) -> dict[str, object]:
    rows = _rows(run, how.file) if how.file in run.files else []
    found = [one for one in rows if one.get("row") == how.row]
    base: dict[str, object] = {
        "id": name,
        "simulated": how.simulated,
        "proposed": name in PROPOSED,
    }
    if not found:
        return base | _unmeasured("not_run", how.absent)
    (row,) = found
    if str(row["status"]).replace(" ", "_") != "measured":
        reason = row.get("reason")
        return base | _unmeasured("not_measured", str(reason) if reason else how.absent)
    met, claimable, caveat = _judgement(name, row)
    return base | {
        "status": "measured",
        "reason": None,
        "figures": how.figures(row, rows),
        "met": met,
        "interval_clears": _maybe_bool(row.get("interval_above")),
        "claimable": claimable,
        "caveat": caveat,
    }


def _unmeasured(status: str, reason: str) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "figures": [],
        "met": None,
        "interval_clears": None,
        "claimable": False,
        "caveat": None,
    }


def _judgement(name: str, row: Row) -> tuple[bool, bool, str | None]:
    """The section's own verdict, whether it may be claimed, and why not."""
    if name == "SC-5":
        met = _bool(row["all_within"])
    elif name in {"SC-8", "SC-9"}:
        met = _bool(row["met"])
    else:
        met = _bool(row["point_meets"])
    if name == "SC-4" and not _bool(row["sc4_window"]):
        days = row["window_days"]
        return met, False, f"measured over {days} days, not the thirty SC-4 is over"
    if name == "SC-8" and met and not _bool(row["claimable"]):
        return met, False, "the injected faults' effects are not reviewed yet"
    return met, met, None


def _rows(run: SnapshotDirectory, name: str) -> list[Row]:
    return [
        json.loads(line)
        for line in run.files[name].decode("utf-8").splitlines()
        if line.strip()
    ]


def _real(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{value!r} is not a number")
    return float(value)


def _maybe_real(value: object) -> float | None:
    return None if value is None else _real(value)


def _maybe_count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{value!r} is not a count")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{value!r} is not true or false")
    return value


def _maybe_bool(value: object) -> bool | None:
    return None if value is None else _bool(value)

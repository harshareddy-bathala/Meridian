"""The reliability section's fault half: saved fault runs, judged again, and SC-5.

A fault run is sealed by ``meridian reliability faults --publish``
(:mod:`meridian.datasets.fault_runs`): its ledger, the evidence the platform
held about every fault, and the verdicts reached then. The report judges every
fault **again**, with the same pure judges, from those files alone, and says
whether it reached the verdicts the run was published with — a judge changed
since would show here as a disagreement, never as a silently different figure.

From the verdicts it reports the roadmap's reliability figures that need an
injected failure's own instant (D-192):

* **the failure-detection distribution** — seconds from each silencing fault to
  the station reading offline, SC-5's detection, by fault kind and overall, with
  the share inside SC-5's 90 s and its Wilson interval;
* **scheduler replan time** — seconds from reading offline to the first
  revocation of the work the station held;
* **alert latency** — seconds from each fault to ``StationOffline`` firing,
  where Prometheus was asked, SC-5's measured half;
* **the 72-hour run** — Stage 24's acceptance item. It counts only from a fault
  run sealed by ``deploy/tools/long_run.py`` with the run's own record inside
  it, and only when every one of these holds:
  - that record spans 72 hours from its start to its end;
  - its own judgement passed;
  - every fault, judged again here, passed;
  - the verdicts reached again are the ones it was published with.

  Otherwise it says why: **not run**, **too short** or **failed** (D-257).

Every station fault is against simulated work, so each of these figures is
**simulated** and labelled so (rule 5); platform faults are counted apart.

Reference: docs/DECISIONS.md D-189, D-192, D-197, D-198, D-240, D-257.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence

from meridian.datasets.fault_runs import FaultRun
from meridian.datasets.long_run_record import LongRunRecord
from meridian.datasets.manifest import content_sha256
from meridian.datasets.weighting import wilson
from meridian.reliability.fault_record import verdict_rows
from meridian.reliability.faults import FaultVerdict, judge_gathered
from meridian.reliability.slis import delays

__all__ = ["LONG_RUN_HOURS", "TIMED", "fault_rows"]

LONG_RUN_HOURS = 72
"""The long run Stage 24 accepts the software on (D-198)."""

TIMED = ("detected", "replanned", "alerted")
"""The checks that time something, and what each times."""

_PLACES = 3

Row = dict[str, object]


def fault_rows(runs: Sequence[FaultRun], *, detection_max_s: int) -> list[Row]:
    """Every run judged again, the timed distributions, SC-5, and the long run.

    Args:
        runs: The fault runs given, verified.
        detection_max_s: SC-5's threshold, from the ``[reliability]`` targets.
    """
    judged = [(run, [judge_gathered(one) for one in run.gathered]) for run in runs]
    verdicts = [one for _, found in judged for one in found]
    rows: list[Row] = [_run(run, found) for run, found in judged]
    for name in TIMED:
        # Replanning is not what SC-5 bounds, so it has no share inside it.
        bound = None if name == "replanned" else detection_max_s
        rows.extend(_timed(name, verdicts, bound))
    rows.extend(_platform(verdicts))
    rows.append(_sc5(verdicts, detection_max_s))
    rows.append(_long_run(judged))
    return rows


def _agrees(run: FaultRun, found: Sequence[FaultVerdict]) -> bool:
    """Whether the verdicts reached again are the ones the run was sealed with."""
    again = [json.loads(json.dumps(one)) for one in verdict_rows(found)]
    return again == list(run.verdicts)


def _run(run: FaultRun, found: Sequence[FaultVerdict]) -> Row:
    return {
        "row": "fault_run",
        "sha256": content_sha256(run.directory.manifest),
        "since": run.directory.manifest.since,
        "as_of": run.directory.manifest.as_of,
        "hours": _hours(run),
        "faults": len(found),
        "failed": sum(not one.passed for one in found),
        "agrees_with_published": _agrees(run, found),
    }


def _timed(
    name: str, verdicts: Sequence[FaultVerdict], threshold: int | None
) -> list[Row]:
    """One check's seconds, overall and by fault kind."""
    by_kind: dict[str, list[float]] = defaultdict(list)
    for verdict in verdicts:
        for check in verdict.checks:
            if check.name == name and check.latency_s is not None:
                by_kind[verdict.fault.kind].append(check.latency_s)
    every = sorted(second for seconds in by_kind.values() for second in seconds)
    rows = [_spread(name, "all", every, threshold)]
    rows.extend(
        _spread(name, kind, sorted(seconds), threshold)
        for kind, seconds in sorted(by_kind.items())
    )
    return rows


def _spread(
    name: str, kind: str, seconds: Sequence[float], threshold: int | None
) -> Row:
    row: Row = {"row": "latency", "check": name, "kind": kind, "simulated": True}
    if not seconds:
        return row | {"n": 0}
    ranked = delays(seconds)
    row |= {
        "n": len(seconds),
        "min_s": _real(seconds[0]),
        "median_s": _real(ranked.p50_s or 0.0),
        "p95_s": _real(ranked.p95_s or 0.0),
        "max_s": _real(seconds[-1]),
        "threshold_s": threshold,
    }
    if threshold is None:
        return row
    inside = sum(1 for one in seconds if one <= threshold)
    rate = wilson(inside / len(seconds), len(seconds))
    return row | {
        "within": inside,
        "within_share": {
            "estimate": _real(rate.estimate),
            "low": _real(rate.low),
            "high": _real(rate.high),
        },
    }


def _platform(verdicts: Sequence[FaultVerdict]) -> list[Row]:
    """Faults done to the platform itself: each check, how often it passed."""
    tally: dict[tuple[str, str], list[bool | None]] = defaultdict(list)
    for verdict in verdicts:
        if verdict.fault.on_platform:
            for check in verdict.checks:
                tally[(verdict.fault.kind, check.name)].append(check.passed)
    return [
        {
            "row": "platform_check",
            "kind": kind,
            "check": name,
            "simulated": False,
            "passed": answers.count(True),
            "failed": answers.count(False),
            "not_applicable": answers.count(None),
        }
        for (kind, name), answers in sorted(tally.items())
    ]


def _sc5(verdicts: Sequence[FaultVerdict], threshold: int) -> Row:
    """SC-5 from detection: the slowest, and the share inside the threshold."""
    seconds = sorted(
        check.latency_s
        for verdict in verdicts
        for check in verdict.checks
        if check.name == "detected" and check.latency_s is not None
    )
    row: Row = {"row": "sc5", "target_s": threshold, "simulated": True}
    if not seconds:
        reason = (
            "no station fault was timed to its station reading offline"
            if verdicts
            else "no fault run was given"
        )
        return row | {"status": "not measured", "reason": reason}
    inside = sum(1 for one in seconds if one <= threshold)
    return row | {
        "status": "measured",
        "n": len(seconds),
        "max_s": _real(seconds[-1]),
        "within": inside,
        "all_within": inside == len(seconds),
    }


def _long_run(judged: Sequence[tuple[FaultRun, Sequence[FaultVerdict]]]) -> Row:
    """Stage 24's 72-hour run: one that counts if any does, else the longest.

    A longer run that failed must not hide a shorter one that is the
    acceptance run, so a run that counts is chosen before length is.
    """
    row: Row = {"row": "long_run", "hours_required": LONG_RUN_HOURS}
    recorded = [
        (run, record, _why_not(run, found, record))
        for run, found in judged
        if (record := run.record) is not None
    ]
    if not recorded:
        return row | {
            "status": "not run",
            "reason": "no fault run carrying a long run's record was given; seal"
            " one with deploy/tools/long_run.py and give it with --faults",
        }
    run, record, reasons = max(
        recorded,
        key=lambda one: (
            one[1].hours >= LONG_RUN_HOURS and not one[2],
            one[1].hours,
        ),
    )
    row |= {
        "sha256": content_sha256(run.directory.manifest),
        "hours": _real(record.hours),
        "interrupted_hours": _real(record.interrupted_hours),
        "seed": record.seed,
        "record": _shown(record.document),
    }
    if record.hours < LONG_RUN_HOURS:
        return row | {"status": "too short", "reasons": reasons}
    if reasons:
        return row | {"status": "failed", "reasons": reasons}
    return row | {"status": "included"}


def _why_not(
    run: FaultRun, found: Sequence[FaultVerdict], record: LongRunRecord
) -> list[str]:
    """Every reason the run is not an acceptance run, its length aside."""
    reasons = list(record.failures)
    failed = sum(not one.passed for one in found)
    if failed:
        reasons.append(f"{failed} of {len(found)} faults failed, judged again")
    if not _agrees(run, found):
        reasons.append(
            "judged again, its verdicts differ from those it was sealed with"
        )
    return reasons


SHOWN = ("environment", "interruptions", "stations", "resources", "alerts_by_name")
"""What of a run's record the report repeats, for a reader to judge the run by."""


def _shown(document: Mapping[str, object]) -> dict[str, object]:
    return {key: document[key] for key in SHOWN if key in document}


def _hours(run: FaultRun) -> float:
    """From the first fault opening to the last closing, or to when it was read."""
    if not run.faults:
        return 0.0
    first = min(one.opened_at for one in run.faults)
    last = max(one.closed_at or run.directory.manifest.as_of for one in run.faults)
    return _real((last - first).total_seconds() / 3600)


def _real(value: float) -> float:
    return round(value, _PLACES)

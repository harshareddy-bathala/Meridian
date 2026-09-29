"""``meridian schedule runs`` and ``meridian passes timing`` — the two views, read.

Each opens one short-lived connection, reads its view, and prints a table. They
are for an operator at a prompt. Neither is a reported number: a view over live
tables answers differently each time, and published figures come from snapshots
(rule 8, D-177). That is why the timing view is read here rather than under
``meridian report``, whose reports are regenerated from a snapshot (Stage 22).

Reference: docs/DECISIONS.md D-025, D-170, D-177.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from meridian.config import load_settings
from meridian.store.operator_views import (
    RunPerformance,
    TimingError,
    find_recent_runs,
    find_timing_errors,
)
from meridian.store.pool import DatabaseUnreachableError, connect_once

__all__ = [
    "add_runs_arguments",
    "add_timing_arguments",
    "print_runs",
    "print_timing",
    "run_pass_timing",
    "run_schedule_runs",
]

EXIT_FAILED = 1
DEFAULT_LIMIT = 20


def add_runs_arguments(parser: argparse.ArgumentParser) -> None:
    """``meridian schedule runs``'s one option."""
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="how many runs, newest first"
    )


def add_timing_arguments(parser: argparse.ArgumentParser) -> None:
    """``meridian passes timing``'s options."""
    parser.add_argument("--station", default=None, help="one station's passes only")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="how many passes, newest first",
    )


def run_schedule_runs(args: argparse.Namespace) -> int:
    """Handle ``meridian schedule runs``."""
    try:
        with connect_once(load_settings()) as conn:
            runs = find_recent_runs(conn, limit=args.limit)
    except (DatabaseUnreachableError, psycopg.Error) as exc:
        return _refuse("schedule runs", str(exc))
    print_runs(runs)
    return 0


def run_pass_timing(args: argparse.Namespace) -> int:
    """Handle ``meridian passes timing``."""
    try:
        with connect_once(load_settings()) as conn:
            errors = find_timing_errors(conn, station_id=args.station, limit=args.limit)
    except (DatabaseUnreachableError, psycopg.Error) as exc:
        return _refuse("passes timing", str(exc))
    print_timing(errors)
    return 0


def print_runs(runs: list[RunPerformance]) -> None:
    """One line per run: what it decided, how the solver did, what came of it."""
    if not runs:
        _say("no schedule run is recorded yet")
        return
    _say(
        "run                decided (UTC)     cfg  solver      sched  skip  "
        "decoded  heard  silent  aborted  missing  revoked  owed  frames"
    )
    for run in runs:
        status = run.solver_status + (" *" if run.fell_back else "")
        _say(
            f"{run.run_id:<18} {run.decided_at:%Y-%m-%d %H:%M}  {run.model_config:<3}"
            f"  {status:<10} {run.scheduled:>6} {run.skipped:>5} {run.decoded:>8}"
            f" {run.signal_no_decode:>6} {run.no_signal:>7} {run.aborted:>8}"
            f" {run.not_attempted:>8} {run.revoked:>8} {run.outstanding:>5}"
            f" {run.frames_decoded:>7}" + ("  simulated" if run.simulated else "")
        )
    if any(run.fell_back for run in runs):
        _say("* the solver fell back to greedy under the same constraints (D-167)")


def print_timing(errors: list[TimingError]) -> None:
    """One line per detected pass, the §6.1 exclusion named where it applies."""
    if not errors:
        _say("no detected pass yet")
        return
    _say(
        "aos (UTC)         station         satellite     raw s   corrected s"
        "  element set age d  excluded"
    )
    for one in errors:
        corrected = "-" if one.timing_error_s is None else f"{one.timing_error_s:+.1f}"
        _say(
            f"{one.aos:%Y-%m-%d %H:%M}  {one.station_id:<15} {one.satellite_id:<13}"
            f" {one.uncorrected_error_s:+7.1f} {corrected:>13}"
            f" {one.element_set_age_days:>18.2f}  {one.excluded or ''}"
            + ("  simulated" if one.simulated else "")
        )
    _say(
        "corrected = first detection + the station's clock offset - predicted aos "
        "(EVALUATION.md §6.1); an operator's read, not a reported figure"
    )


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(command: str, reason: str) -> int:
    print(f"meridian {command}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

"""``meridian schedule`` — the operator's way to run the scheduler.

Parses the horizon and the configuration, opens one short-lived connection, runs
``meridian.scheduler.run`` against the real propagator, and prints what was
decided. The decisions themselves are the scheduler's; everything here is
argument handling, exit codes and rendering.

Split out of ``meridian.cli`` for the same reason ``cli_passes`` is: that module
owns the command tree, and this is a subcommand whose implementation needs more
than a handler and a print.

Reference: docs/DECISIONS.md D-065, D-066.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from meridian.cli_passes import parse_horizon_bound
from meridian.config import load_settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.scheduler.run import (
    RANKERS,
    ScheduleReport,
    ScheduleRequest,
    run_schedule,
)
from meridian.store.pool import CONNECT_TIMEOUT_S

__all__ = ["PHASE_1_TURNAROUND_S", "run_scheduler"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``. Importing it from there would be a
cycle: ``cli`` imports this module to dispatch to it."""

PHASE_1_TURNAROUND_S = 0.0
"""Seconds a station needs between two receptions, for slew and settling.

Zero because Phase 1 receives on a fixed quadrifilar helix at 137 MHz, which
does not move: there is nothing to slew and nothing to settle, so any positive
value here would discard passes the station could genuinely have taken.
docs/PROJECT.md makes the tracking build (a crossed Yagi on a rotator) Tier 3
and explicitly optional — *"every claim the project makes is provable with a
fixed antenna"* — and the simulated stations are fixed by construction too.

**This is correct for the hardware that exists, not a simplification.** The
moment a station registers with a rotator it needs a measured value of its own,
and that needs a column and a measurement rather than a constant here — see
D-066.
"""


def _print_schedule_report(report: ScheduleReport) -> None:
    """Write one run's outcome to stdout, in the order an operator reads it."""
    already_held = report.scheduled + report.skipped - report.rows_written

    print(f"  configuration:       {report.model_config}")  # noqa: T201
    print(f"  stations considered: {report.stations_considered}")  # noqa: T201
    print(f"  passes considered:   {report.candidates_considered}")  # noqa: T201
    print(f"  scheduled:           {report.scheduled}")  # noqa: T201
    print(f"  skipped:             {report.skipped}")  # noqa: T201
    print(  # noqa: T201
        f"  rows written:        {report.rows_written} ({already_held} already held)"
    )

    if report.passes_without_a_usable_transmitter:
        # Normally empty — pass generation applies the same capability test. It
        # fills when the catalogue changed since, and naming the passes is what
        # separates that from a satellite that simply never rose.
        count = len(report.passes_without_a_usable_transmitter)
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"  {count} pass(es) had no downlink this station can receive; "
            f"the catalogue may have changed since they were generated",
            file=sys.stderr,
        )


def run_scheduler(args: argparse.Namespace) -> int:
    """Run ``meridian schedule``.

    Args:
        args: The parsed command line, carrying ``start``, ``end`` and
            ``model_config``.

    Returns:
        ``0`` on a completed run, including one that wrote nothing — the
        expected result of re-running over an unchanged horizon.
        ``meridian.cli.EXIT_FAILED`` when the horizon could not be parsed or the
        database could not be reached.
    """
    try:
        start = parse_horizon_bound(args.start, "--from")
        end = parse_horizon_bound(args.end, "--to")
    except ValueError as exc:
        print(f"meridian schedule: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    request = ScheduleRequest(
        start=start,
        end=end,
        model_config=args.model_config,
        turnaround_s=PHASE_1_TURNAROUND_S,
    )

    settings = load_settings()
    try:
        conn = psycopg.connect(settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian schedule: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    with conn:
        report = run_schedule(conn, SkyfieldOrbitService(), request)

    print(  # noqa: T201
        f"Scheduled [{start.isoformat()}, {end.isoformat()}) "
        f"under configuration {args.model_config}"
    )
    _print_schedule_report(report)
    return 0


def configurations() -> list[str]:
    """The configurations ``--config`` accepts, so the parser and the run agree.

    Read from the scheduler's own table rather than written out here. A parser
    listing a configuration the run does not implement would reject the command
    at the wrong layer, with a worse message.
    """
    return sorted(RANKERS)

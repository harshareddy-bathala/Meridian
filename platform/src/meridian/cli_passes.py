"""``meridian passes generate`` — the operator's way to run pass generation.

Parses the two horizon arguments, opens one short-lived connection, runs
``meridian.pass_generation`` against the real propagator, and prints what
happened. The work itself is the job module's; everything here is argument
handling, exit codes and rendering.

Split out of ``meridian.cli`` rather than added to it: the command tree, its
three subcommands and their parsers had already brought that module close to the
400-line limit, and this is the first subcommand whose implementation needs more
than a handler and a print. ``cli`` keeps the parser and the dispatch, so a
reader still finds the whole command surface in one file.

Reference: docs/DECISIONS.md D-063, D-064.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import psycopg

from meridian.config import load_settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import require_utc
from meridian.pass_generation import (
    GenerationHorizon,
    GenerationReport,
    generate_passes,
)
from meridian.store.pool import CONNECT_TIMEOUT_S

__all__ = ["parse_horizon_bound", "run_passes"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``. Imported from there would be a cycle:
``cli`` imports this module to dispatch to it."""


def parse_horizon_bound(value: str, flag: str) -> datetime:
    """One ``--from``/``--to`` argument as a timezone-aware UTC instant.

    Public because ``meridian schedule`` takes the same two flags and has to
    reject the same input the same way; a second copy would drift.

    Args:
        value: The argument exactly as typed, ISO-8601.
        flag: The flag it came from, so a rejection names what to fix.

    Returns:
        The parsed instant.

    Raises:
        ValueError: The text is not ISO-8601, or carries no offset, or carries
            one that is not UTC. A bare ``2026-08-11T00:00:00`` is refused
            rather than assumed to be UTC — the operator running this may not be
            in UTC, and a horizon silently shifted by their local offset would
            generate a day of passes for the wrong day.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{flag} is not an ISO-8601 instant: {value!r}") from exc

    return require_utc(parsed, flag)


def _print_generation_report(report: GenerationReport) -> None:
    """Write one run's outcome to stdout, in the order an operator reads it."""
    already_held = report.passes_computed - report.passes_stored

    print(f"  stations considered: {report.stations_considered}")  # noqa: T201
    print(f"  pairs propagated:    {report.pairs_propagated}")  # noqa: T201
    print(f"  passes computed:     {report.passes_computed}")  # noqa: T201
    print(  # noqa: T201
        f"  passes stored:       {report.passes_stored} ({already_held} already held)"
    )

    if report.satellites_without_element_set:
        # Not an error: the object is tracked and its transmitter is live, so
        # this is a gap in the archive an operator can close by importing
        # elements — silence here would look like the satellite has no passes.
        missing = ", ".join(report.satellites_without_element_set)
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"  no element set current at the horizon start for: {missing}",
            file=sys.stderr,
        )


def run_passes(args: argparse.Namespace) -> int:
    """Run ``meridian passes generate``, the subcommand's only action.

    Args:
        args: The parsed command line, carrying ``start`` and ``end`` as typed.

    Returns:
        ``0`` on a completed run — including one that stored nothing, which is
        the expected result of re-running over an unchanged horizon.
        ``meridian.cli.EXIT_FAILED`` when the horizon could not be parsed or the
        database could not be reached.

    Note:
        Its own connection for the invocation, like ``meridian invite`` and
        ``meridian station``: a CLI call is a single short-lived process and
        there is nothing here for a pool to amortize.
    """
    try:
        horizon = GenerationHorizon(
            start=parse_horizon_bound(args.start, "--from"),
            end=parse_horizon_bound(args.end, "--to"),
        )
    except ValueError as exc:
        print(f"meridian passes generate: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    settings = load_settings()
    try:
        conn = psycopg.connect(settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian passes: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    with conn:
        report = generate_passes(conn, SkyfieldOrbitService(), horizon)

    print(  # noqa: T201
        f"Generated passes over "
        f"[{horizon.start.isoformat()}, {horizon.end.isoformat()})"
    )
    _print_generation_report(report)
    return 0

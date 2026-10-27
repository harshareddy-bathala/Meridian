"""``meridian watch`` — each receive chain's signal against its own past.

``baseline`` trains every chain that needs a baseline from its own heard
receptions over the training interval (D-293), the same work the jobs service
does each round. A chain with an open warning is not trained again.

Reference: docs/DECISIONS.md D-290, D-291, D-293.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.config import load_settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.reliability.config import (
    RELIABILITY_CONFIG_ENV,
    ReliabilityConfig,
    ReliabilityConfigError,
    load_deployed_reliability_config,
    load_reliability_config,
)
from meridian.reliability.watch_build import BaselineRunReport, build_baselines
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.stations import Connection

__all__ = ["add_watch_parser", "baseline_lines", "run_watch"]

EXIT_FAILED = 1

_REFUSED = (
    ReliabilityConfigError,
    DatabaseUnreachableError,
    OSError,
    psycopg.Error,
    ValueError,
)


def add_watch_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian watch`` and its actions."""
    watch = subcommands.add_parser(
        "watch",
        help="watch each receive chain's signal against its own past",
        description=(
            "A receive chain's signal at each elevation, against a baseline "
            "trained from its own heard receptions (D-290, D-293)."
        ),
    )
    watch.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"reliability settings (default: ${RELIABILITY_CONFIG_ENV}, else "
        "the defaults in deploy/reliability.toml.example)",
    )
    actions = watch.add_subparsers(dest="action", metavar="<action>")
    baseline = actions.add_parser(
        "baseline", help="train every chain that needs a baseline"
    )
    baseline.add_argument(
        "--station", default=None, help="one station's chains (default: every one)"
    )


def run_watch(args: argparse.Namespace) -> int:
    """Handle every ``meridian watch`` action."""
    action = {"baseline": _baseline}[args.action]
    try:
        config = (
            load_reliability_config(args.config)
            if args.config is not None
            else load_deployed_reliability_config()
        )
        with connect_once(load_settings()) as conn:
            lines = action(conn, args, config)
    except _REFUSED as exc:
        return _refuse(args.action, str(exc))
    for line in lines:
        _say(line)
    return 0


def _baseline(
    conn: Connection, args: argparse.Namespace, config: ReliabilityConfig
) -> list[str]:
    report = build_baselines(
        conn,
        SkyfieldOrbitService(),
        config=config,
        now=datetime.now(UTC),
        station_id=args.station,
    )
    conn.commit()
    return baseline_lines(report)


def baseline_lines(report: BaselineRunReport) -> list[str]:
    """One run's outcome, as printed."""
    lines = [
        f"baselines by {report.method}, settings {report.config_sha256.hex()[:12]}",
        f"  interval           {report.trained_from:%Y-%m-%d} to"
        f" {report.trained_until:%Y-%m-%d}",
        f"  chains heard       {report.chains}",
        f"  written            {report.written} ({report.simulated} simulated)",
        f"  recent enough      {report.kept}",
        f"  held, warning open {report.held}",
    ]
    if report.ambiguous:
        lines.append(
            f"  left out           {report.ambiguous} receptions no single"
            " capability covers"
        )
    lines.extend(f"  too little history {one}" for one in report.too_little)
    lines.extend(f"  unreadable         {one}" for one in report.unreadable)
    return lines


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian watch {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

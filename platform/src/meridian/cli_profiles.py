"""``meridian profiles build`` — write the horizon and interference profiles.

Opens one short-lived connection, runs :mod:`meridian.profile_build` over the
datasets root, commits, and prints what happened. The jobs service runs the same
function each round, so this command is for an operator who has just labelled a
dataset or changed a mask and does not want to wait.

Run twice, the second run writes nothing and says ``already held``: a learned
profile is identified by its dataset, and a declared one is written only when
its mask changes (D-174).

Reference: docs/DECISIONS.md D-174, D-175.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from meridian.cli_snapshot import (
    DATASETS_ROOT_ENV,
    DEFAULT_DATASETS_ROOT,
    datasets_root,
)
from meridian.config import load_settings
from meridian.prediction.live import LiveScoringError
from meridian.profile_build import ProfileBuildReport, build_profiles_apart
from meridian.store.pool import DatabaseUnreachableError, connect_once

__all__ = ["add_profiles_parser", "print_profile_report", "run_profiles"]

EXIT_FAILED = 1

_REFUSED = (DatabaseUnreachableError, LiveScoringError, OSError, psycopg.Error)


def add_profiles_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian profiles`` and its one action."""
    profiles = subcommands.add_parser(
        "profiles",
        help="write the declared and learned horizon and interference profiles",
        description=(
            "Declared masks are written when they change; learned profiles are "
            "built once from the newest labelled dataset (D-174, D-175)."
        ),
    )
    profiles.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            f"datasets root (default: ${DATASETS_ROOT_ENV}, "
            f"else {DEFAULT_DATASETS_ROOT})"
        ),
    )
    actions = profiles.add_subparsers(dest="action", metavar="<action>")
    actions.add_parser("build", help="write what has changed, and say what was held")


def run_profiles(args: argparse.Namespace) -> int:
    """Handle ``meridian profiles build``."""
    root = datasets_root(args.root)
    try:
        settings = load_settings()
        report = build_profiles_apart(lambda: connect_once(settings), root)
    except _REFUSED as exc:
        return _refuse(str(exc))
    print_profile_report(report)
    return 0


def print_profile_report(report: ProfileBuildReport) -> None:
    """Write one build's outcome to stdout."""
    _say(
        f"  declared masks:      {report.declared_capabilities}"
        f" ({report.declared_written} changed and written)"
    )
    if report.dataset is None:
        _say("  learned:             no labelled dataset yet; nothing to build")
        return
    as_of = "" if report.dataset_as_of is None else report.dataset_as_of.isoformat()
    _say(f"  dataset:             {report.dataset.name} (as of {as_of})")
    if report.already_built:
        _say("  learned:             already held, identically")
        return
    _say(f"  stations built:      {report.stations_built}")
    _say(f"  horizon rows:        {report.horizon_rows}")
    _say(f"  interference rows:   {report.interference_rows}")


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(reason: str) -> int:
    print(f"meridian profiles build: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

"""``meridian diagnosis`` — why each lost reception was lost, or *undetermined*.

``run`` diagnoses every classified loss not yet diagnosed under the deployed
method and ``[diagnosis]`` thresholds (D-272), the same work the jobs service
does each round. ``explain`` prints an assignment's diagnoses with every cause
that was tested and what its test found (D-273).

**A partial reception needs a verdict model.** A decode is read as partial
against the model ``VERDICT_MODEL`` names, or the one given with
``--verdict-method``; with neither, no decode is diagnosed, and ``run`` says so.

Reference: docs/DECISIONS.md D-102, D-272, D-273.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.config import Settings, load_settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.prediction.score import MalformedModelError
from meridian.prediction.verdict_files import DamagedVerdictError, load_verdict_model
from meridian.reliability.config import (
    RELIABILITY_CONFIG_ENV,
    ReliabilityConfig,
    ReliabilityConfigError,
    load_deployed_reliability_config,
    load_reliability_config,
)
from meridian.reliability.diagnosis_run import DiagnosisRunReport, diagnose_settled
from meridian.store.loss_diagnoses import StoredDiagnosis, find_diagnoses
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.stations import Connection
from meridian.verdict_build import registry_for

__all__ = ["add_diagnosis_parser", "explain_lines", "run_diagnosis", "run_lines"]

EXIT_FAILED = 1

_REFUSED = (
    ReliabilityConfigError,
    MalformedModelError,
    DatabaseUnreachableError,
    OSError,
    psycopg.Error,
    ValueError,
)


def add_diagnosis_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian diagnosis`` and its actions."""
    diagnosis = subcommands.add_parser(
        "diagnosis",
        help="diagnose why each lost reception was lost, from our own records",
        description=(
            "One cause per loss, or undetermined: satellite silent, station not "
            "listening, obstruction, interference or a timing fault (D-272, "
            "D-273). Every cause tested is recorded with what its test found."
        ),
    )
    diagnosis.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"reliability settings (default: ${RELIABILITY_CONFIG_ENV}, else "
        "the defaults in deploy/reliability.toml.example)",
    )
    actions = diagnosis.add_subparsers(dest="action", metavar="<action>")
    run = actions.add_parser("run", help="diagnose every classified loss not yet")
    run.add_argument(
        "--limit", type=int, default=None, help="at most this many, oldest first"
    )
    run.add_argument(
        "--verdict-method",
        default=None,
        metavar="METHOD",
        help="the verdict a decode is read against (default: VERDICT_MODEL's)",
    )
    explain = actions.add_parser(
        "explain", help="print an assignment's diagnoses and every cause tested"
    )
    explain.add_argument("assignment_id")


def run_diagnosis(args: argparse.Namespace) -> int:
    """Handle every ``meridian diagnosis`` action."""
    action = {"run": _run, "explain": _explain}[args.action]
    try:
        config = (
            load_reliability_config(args.config)
            if args.config is not None
            else load_deployed_reliability_config()
        )
        settings = load_settings()
        with connect_once(settings) as conn:
            lines = action(conn, args, settings, config)
    except DamagedVerdictError as exc:
        _refuse(args.action, str(exc))
        return EXIT_CORRUPT
    except _REFUSED as exc:
        return _refuse(args.action, str(exc))
    for line in lines:
        _say(line)
    return 0


def _run(
    conn: Connection,
    args: argparse.Namespace,
    settings: Settings,
    config: ReliabilityConfig,
) -> list[str]:
    verdict_method = args.verdict_method or (
        load_verdict_model(Path(settings.verdict_model)).method
        if settings.verdict_model
        else None
    )
    now = datetime.now(UTC)
    report = diagnose_settled(
        conn,
        registry_for(conn, settings, now),
        SkyfieldOrbitService(),
        config=config,
        verdict_method=verdict_method,
        limit=args.limit,
    )
    conn.commit()
    return run_lines(report)


def run_lines(report: DiagnosisRunReport) -> list[str]:
    """One run's outcome, as printed."""
    causes = " · ".join(f"{name} {n}" for name, n in report.by_cause.items() if n)
    partial = (
        f"decodes read against {report.verdict_method}"
        if report.verdict_method
        else "no verdict model: no decode is diagnosed as partial"
    )
    lines = [
        f"diagnoses by {report.method}, thresholds {report.config_sha256.hex()[:12]}",
        f"  diagnosed          {report.diagnosed} ({report.simulated} simulated)",
        f"  written            {report.written}",
        f"  causes             {causes or 'none'}",
        f"  partial            {partial}",
    ]
    lines.extend(f"  unreadable         {one}" for one in report.unreadable)
    if report.deferred:
        lines.append("  more remain; the next run continues")
    return lines


def _explain(
    conn: Connection,
    args: argparse.Namespace,
    _settings: Settings,
    _config: ReliabilityConfig,
) -> list[str]:
    return explain_lines(args.assignment_id, find_diagnoses(conn, args.assignment_id))


def explain_lines(assignment_id: str, rows: list[StoredDiagnosis]) -> list[str]:
    """Every diagnosis of one assignment, each with every cause it tested."""
    if not rows:
        return [f"{assignment_id}: no diagnosis (not lost, or not yet classified)"]
    lines = []
    for row in rows:
        label = " (simulated)" if row.simulated else ""
        revision = "no report" if row.revision is None else f"revision {row.revision}"
        lines.append(
            f"{row.assignment_id} {revision}{label}: {row.cause}"
            f"  [{row.method}, thresholds {row.config_sha256.hex()[:12]},"
            f" classification {row.classification_id}]"
        )
        for one in row.candidates_json:
            mark = "fired" if one.get("fired") else "     "
            found = json.dumps(one.get("found", {}), sort_keys=True)
            lines.append(
                f"    {mark}  {one.get('cause')!s:<22} support {one.get('support')}"
                f"  {found}"
            )
    return lines


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian diagnosis {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

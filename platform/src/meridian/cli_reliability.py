"""``meridian reliability`` — sweep, classify, report, explain, and judge faults.

Four verbs over the live record, each of which the jobs service also runs or
the API also serves, so an operator can do by hand what the platform does on
its own and see the same answer — and a fifth that judges a fault run:

* ``sweep`` expires every scheduled assignment whose window closed untaken
  (D-183);
* ``classify`` classifies every pass that has settled and stores it with its
  evidence (D-182);
* ``report [--at …]`` prints the reliability figures for the window ending now,
  or at a stated instant (D-184, D-185);
* ``explain <assignment id>`` prints every stored classification of that
  assignment's pass, with the evidence it was decided from. It is how any
  figure the report prints is traced back to its rows (Stage 20's gate);
* ``faults --ledger PATH`` judges every fault a run's ledger records against
  what the platform stored, and exits non-zero if any check failed (D-192).

Every verb takes ``--config``, else :data:`RELIABILITY_CONFIG_ENV`, else the
defaults, which ``deploy/reliability.toml.example`` spells out.

Reference: docs/DECISIONS.md D-182, D-183, D-184, D-185, D-192.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.config import load_settings
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.reliability.accounting import classify_settled
from meridian.reliability.config import (
    RELIABILITY_CONFIG_ENV,
    ReliabilityConfig,
    ReliabilityConfigError,
    load_deployed_reliability_config,
    load_reliability_config,
)
from meridian.reliability.fault_check import check_faults, prometheus_alert_history
from meridian.reliability.faults import (
    FaultLedgerError,
    FaultVerdict,
    read_fault_ledger,
)
from meridian.reliability.live import read_live_report
from meridian.reliability.report import report_lines
from meridian.store.assignment_expiry import expire_untaken_assignments
from meridian.store.pass_classifications import (
    StoredClassification,
    find_classifications_of,
)
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.stations import Connection

__all__ = ["add_reliability_parser", "run_reliability"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``."""


def add_reliability_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian reliability`` and its five actions."""
    reliability = subcommands.add_parser(
        "reliability",
        help="classify settled passes and report reliability from them",
        description=(
            "A pass counts as missed only if the station was confirmed "
            "listening (CLAUDE.md rule 7). Every figure is counted from stored "
            "classifications, each of which keeps its evidence (D-182)."
        ),
    )
    reliability.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"reliability settings (default: ${RELIABILITY_CONFIG_ENV}, else "
        "the defaults in deploy/reliability.toml.example)",
    )
    actions = reliability.add_subparsers(dest="action", metavar="<action>")
    actions.add_parser("sweep", help="expire assignments nobody took")
    actions.add_parser("classify", help="classify every pass that has settled")
    report = actions.add_parser("report", help="print the reliability figures")
    report.add_argument(
        "--at",
        type=_instant,
        default=None,
        help="ISO-8601 UTC end of the window (default: now)",
    )
    explain = actions.add_parser(
        "explain", help="print a pass's classification and its evidence"
    )
    explain.add_argument("assignment_id", help="any assignment of the pass")
    faults = actions.add_parser(
        "faults", help="judge a fault run's ledger against what was stored"
    )
    faults.add_argument(
        "--ledger",
        type=Path,
        required=True,
        help="the run's fault ledger, or - to read it from standard input",
    )
    faults.add_argument(
        "--prometheus",
        default=None,
        metavar="URL",
        help="also read when StationOffline fired, from this Prometheus",
    )
    faults.add_argument(
        "--json", type=Path, default=None, metavar="PATH", help="also write JSON"
    )


def run_reliability(args: argparse.Namespace) -> int:
    """Run one ``meridian reliability`` action."""
    try:
        config = (
            load_reliability_config(args.config)
            if args.config is not None
            else load_deployed_reliability_config()
        )
    except ReliabilityConfigError as exc:
        return _refuse(args.action, str(exc))
    actions = {
        "sweep": _sweep,
        "classify": _classify,
        "report": _report,
        "explain": _explain,
        "faults": _faults,
    }
    now = datetime.now(UTC)
    try:
        with connect_once(load_settings()) as conn:
            return actions[args.action](conn, args, config, now)
    except (DatabaseUnreachableError, psycopg.Error) as exc:
        return _refuse(args.action, f"the database did not answer: {exc}")


def _sweep(
    conn: Connection,
    _args: argparse.Namespace,
    _config: ReliabilityConfig,
    now: datetime,
) -> int:
    expired = expire_untaken_assignments(conn, now=now)
    _say(f"expired {expired} scheduled assignments nobody took")
    return 0


def _classify(
    conn: Connection,
    _args: argparse.Namespace,
    config: ReliabilityConfig,
    now: datetime,
) -> int:
    settings = load_settings()
    registry = PsycopgRegistry(
        conn,
        pepper=settings.token_hash_pepper,
        recovery_window_s=settings.registration_recovery_window_s,
        now_utc=now,
    )
    report = classify_settled(conn, registry, now=now, config=config.classification)
    _say(
        f"expired {report.expired} untaken assignments; classified "
        f"{report.classified} passes settled by {report.settled_by.isoformat()}; "
        f"{report.written} rows written"
    )
    for name, count in report.by_class.items():
        if count:
            _say(f"  {name:<32} {count}")
    return 0


def _report(
    conn: Connection, args: argparse.Namespace, config: ReliabilityConfig, now: datetime
) -> int:
    report = read_live_report(conn, now=args.at or now, config=config)
    for line in report_lines(report):
        _say(line)
    return 0


def _explain(
    conn: Connection,
    args: argparse.Namespace,
    _config: ReliabilityConfig,
    _now: datetime,
) -> int:
    held = find_classifications_of(conn, args.assignment_id)
    if not held:
        return _refuse(
            "explain",
            f"{args.assignment_id} is in no classified pass: it is not a "
            "scheduled assignment, or its window has not settled yet",
        )
    for one in held:
        for line in _explained(one):
            _say(line)
    return 0


def _faults(
    conn: Connection,
    args: argparse.Namespace,
    _config: ReliabilityConfig,
    now: datetime,
) -> int:
    try:
        if str(args.ledger) == "-":
            # So a ledger on the host can be judged inside the API's container,
            # whose filesystem is read-only (D-206): piped, not copied in.
            faults = read_fault_ledger(sys.stdin)
        else:
            with args.ledger.open(encoding="utf-8") as handle:
                faults = read_fault_ledger(handle)
    except (OSError, FaultLedgerError) as exc:
        return _refuse("faults", f"cannot read {args.ledger}: {exc}")
    alerts = prometheus_alert_history(args.prometheus) if args.prometheus else None
    try:
        verdicts = check_faults(conn, faults, now=now, alerts=alerts)
    except (OSError, ValueError) as exc:
        # A Prometheus that is restarting or slow is a refusal to judge the
        # alerts, said as one, not a traceback with nothing printed.
        return _refuse("faults", f"Prometheus did not answer: {exc}")
    for verdict in verdicts:
        for line in _judged(verdict):
            _say(line)
    failed = sum(not one.passed for one in verdicts)
    _say(f"{len(verdicts)} faults judged, {failed} failed")
    if args.json is not None:
        args.json.write_text(
            json.dumps([_as_json(one) for one in verdicts], indent=2) + "\n",
            encoding="utf-8",
        )
    return EXIT_FAILED if failed else 0


_MARKS = {True: "pass", False: "FAIL", None: "  - "}


def _judged(verdict: FaultVerdict) -> list[str]:
    fault = verdict.fault
    closed = fault.closed_at.isoformat() if fault.closed_at else "still open"
    return [
        f"{'ok  ' if verdict.passed else 'FAIL'} {fault.kind} on {fault.target} "
        f"({fault.station_id or 'platform'}), {fault.opened_at.isoformat()} … {closed}",
        *(
            f"       {_MARKS[one.passed]}  {one.name:<18} {one.detail}"
            for one in verdict.checks
        ),
    ]


def _as_json(verdict: FaultVerdict) -> dict[str, object]:
    fault = verdict.fault
    return {
        "run_id": fault.run_id,
        # Injected, and against a station only the simulator drives: a station
        # fault's verdict is about simulated work (rule 5). A platform fault
        # was done to the platform itself, which is not simulated.
        "simulated": not fault.on_platform,
        "kind": fault.kind,
        "target": fault.target,
        "station_id": fault.station_id,
        "opened_at": fault.opened_at.isoformat(),
        "closed_at": fault.closed_at.isoformat() if fault.closed_at else None,
        "passed": verdict.passed,
        "checks": [
            {"name": one.name, "passed": one.passed, "detail": one.detail}
            for one in verdict.checks
        ],
    }


def _explained(one: StoredClassification) -> list[str]:
    population = "simulated" if one.simulated else "measured"
    return [
        f"pass {one.pass_id} at {one.station_id}, {one.satellite_id} ({population})",
        f"  window             {one.window_start.isoformat()} … "
        f"{one.window_end.isoformat()}",
        f"  assignments        {', '.join(one.assignment_ids)}",
        f"  classification     {one.classification}",
        f"  classified by      {one.method}, parameters {one.config_sha256.hex()}",
        f"  classified at      {one.classified_at.isoformat()}",
        "  evidence",
        *(
            f"    {line}"
            for line in json.dumps(one.evidence, indent=2, sort_keys=True).splitlines()
        ),
    ]


def _instant(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text} has no time zone; add Z or +00:00")
    return parsed.astimezone(UTC)


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian reliability {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED

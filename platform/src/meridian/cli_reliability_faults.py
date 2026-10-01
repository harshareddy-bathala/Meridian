"""``meridian reliability faults``: judge a fault run's ledger, and seal it.

Split from :mod:`meridian.cli_reliability`, which keeps the parser and the
other actions. This is the one action that writes a directory: with
``--publish`` it seals the ledger, the evidence read for each fault and the
verdicts, plus a long run's own record when ``--run-record`` names one, as a
fault run an evaluation report can judge again (D-240, D-257).

Reference: docs/DECISIONS.md D-189, D-192, D-240, D-257.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Sequence
from datetime import datetime

from meridian.cli_snapshot import datasets_root
from meridian.datasets.fault_runs import publish_fault_run
from meridian.datasets.long_run_record import (
    LongRunRecord,
    LongRunRecordError,
    parse_long_run_record,
)
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import DamagedSnapshotError
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.fault_check import gather_evidence, prometheus_alert_history
from meridian.reliability.faults import (
    FaultLedgerError,
    FaultVerdict,
    Gathered,
    InjectedFault,
    judge_gathered,
    read_fault_ledger,
)
from meridian.store.schema_revision import find_current_revision
from meridian.store.stations import Connection

__all__ = ["judge_faults"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``."""


def judge_faults(
    conn: Connection,
    args: argparse.Namespace,
    _config: ReliabilityConfig,
    now: datetime,
) -> int:
    """Judge every fault in the ledger from what the platform stored, and print it.

    Returns:
        0 when every fault passed, 1 when one failed or the run was refused.
    """
    read = _fault_inputs(args)
    if isinstance(read, str):
        return _refuse("faults", read)
    ledger, faults, record = read
    alerts = prometheus_alert_history(args.prometheus) if args.prometheus else None
    try:
        gathered = gather_evidence(conn, faults, now=now, alerts=alerts)
    except (OSError, ValueError) as exc:
        # A Prometheus that is restarting or slow is a refusal to judge the
        # alerts, said as one, not a traceback with nothing printed.
        return _refuse("faults", f"Prometheus did not answer: {exc}")
    verdicts = tuple(judge_gathered(one) for one in gathered)
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
    if args.publish and not _publish(
        conn, args, (ledger, gathered, verdicts, record), now
    ):
        return EXIT_FAILED
    return EXIT_FAILED if failed else 0


def _fault_inputs(
    args: argparse.Namespace,
) -> tuple[str, tuple[InjectedFault, ...], LongRunRecord | None] | str:
    """The ledger, its faults and any run record; or why they cannot be read."""
    if args.run_record is not None and not args.publish:
        return "--run-record is sealed with the run: give --publish"
    try:
        # "-" so a ledger on the host can be judged inside the API's container,
        # whose filesystem is read-only (D-206): piped, not copied in.
        ledger = (
            sys.stdin.read()
            if str(args.ledger) == "-"
            else args.ledger.read_text(encoding="utf-8")
        )
        faults = read_fault_ledger(io.StringIO(ledger))
    except (OSError, FaultLedgerError) as exc:
        return f"cannot read {args.ledger}: {exc}"
    try:
        record = (
            None
            if args.run_record is None
            else parse_long_run_record(args.run_record.read_bytes())
        )
    except (OSError, LongRunRecordError) as exc:
        return f"cannot read {args.run_record}: {exc}"
    return ledger, faults, record


def _publish(
    conn: Connection,
    args: argparse.Namespace,
    run: tuple[str, Sequence[Gathered], Sequence[FaultVerdict], LongRunRecord | None],
    now: datetime,
) -> bool:
    """Seal what was read and judged, so a report can judge it again (D-240).

    Returns False, having said why, when it could not be sealed — a read-only
    filesystem, as inside the API's container (D-206), or a clash on disk.
    """
    ledger, gathered, verdicts, record = run
    try:
        published = publish_fault_run(
            ledger,
            gathered,
            verdicts,
            root=datasets_root(args.root),
            stamp=(find_current_revision(conn) or "unknown", now),
            record=record,
        )
    except (OSError, ValueError, DamagedSnapshotError) as exc:
        _refuse("faults", f"the fault run was judged but not published: {exc}")
        return False
    held = "written" if published.written else "already held, identically"
    _say(f"fault run: {published.path} ({held})")
    _say(f"  hash               {content_sha256(published.manifest).hex()}")
    return True


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
            {
                "name": one.name,
                "passed": one.passed,
                "detail": one.detail,
                "latency_s": one.latency_s,
            }
            for one in verdict.checks
        ],
    }


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian reliability {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED

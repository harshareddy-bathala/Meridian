"""``meridian verdict`` — the reception verdict, and its label rated blind.

``queue`` lists the measured receptions waiting for a rating. ``rate`` records
one. ``apply`` writes a fitted model's verdict for every closed reception it
has not scored (D-263). ``fit`` and ``evaluate`` work on files, not the
database, and live in :mod:`meridian.cli_verdict_model`.

The label is D-260's answer to D-106: a person looks at the decoded product
and says whether it is usable, without seeing the verdict or anything the
verdict reads.

**The queue prints what to look at and nothing else**: the reception's
identity, its time, and each product's kind, hash and place on the station.
The outcome, SNR, frame counts and noise floor are not read
(:mod:`meridian.store.ratings`), so a rater cannot be steered by them.

Reference: docs/DECISIONS.md D-106, D-260.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.cli_verdict_model import (
    MODEL_ACTIONS,
    add_model_actions,
    run_model_action,
)
from meridian.config import Settings, load_settings
from meridian.prediction.score import MalformedModelError
from meridian.prediction.verdict_files import DamagedVerdictError, load_verdict_model
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.ratings import (
    NewRating,
    QueuedReception,
    RatingRefusedError,
    insert_rating,
    unrated_receptions,
)
from meridian.store.stations import Connection
from meridian.verdict_build import VerdictBuildReport, apply_verdicts, registry_for

__all__ = [
    "DEFAULT_RUBRIC",
    "add_verdict_parser",
    "apply_lines",
    "queue_lines",
    "run_verdict",
]

EXIT_FAILED = 1

DEFAULT_RUBRIC = "usable-1"
"""The written rating instructions in ``docs/OPERATIONS.md`` § Reception verdicts."""

_REFUSED = (
    RatingRefusedError,
    MalformedModelError,
    DatabaseUnreachableError,
    OSError,
    psycopg.Error,
)


def add_verdict_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian verdict`` and its actions."""
    verdict = subcommands.add_parser(
        "verdict",
        help="rate receptions blind, the label the reception verdict learns",
        description=(
            "A rating is a person's answer to whether a decoded product is "
            "usable, made without the verdict or its inputs (D-106, D-260)."
        ),
    )
    actions = verdict.add_subparsers(dest="action", metavar="<action>")
    actions.add_parser(
        "queue", help="list measured receptions with products and no rating"
    )
    rate = actions.add_parser("rate", help="record one rating of one reception")
    rate.add_argument("assignment", help="the reception's assignment id")
    rate.add_argument("revision", type=int, help="the observation revision rated")
    answer = rate.add_mutually_exclusive_group(required=True)
    answer.add_argument("--usable", dest="usable", action="store_true")
    answer.add_argument("--unusable", dest="usable", action="store_false")
    rate.add_argument(
        "--rater",
        required=True,
        help="a short tag for whoever rated, not a name: [a-z0-9_-], up to 16",
    )
    rate.add_argument(
        "--rubric",
        default=DEFAULT_RUBRIC,
        help=f"the rating instructions followed (default {DEFAULT_RUBRIC})",
    )
    apply = actions.add_parser(
        "apply", help="write a verdict for every closed reception a model has not"
    )
    apply.add_argument(
        "--model", type=Path, required=True, help="a verdict model directory"
    )
    add_model_actions(actions)


def run_verdict(args: argparse.Namespace) -> int:
    """Handle every ``meridian verdict`` action."""
    if args.action in MODEL_ACTIONS:
        return run_model_action(args)
    return _run_database(args)


def _run_database(args: argparse.Namespace) -> int:
    """``queue``, ``rate`` and ``apply``, on one short-lived connection."""
    action = {"queue": _queue, "rate": _rate, "apply": _apply}[args.action]
    try:
        settings = load_settings()
        with connect_once(settings) as conn:
            lines = action(conn, args, settings)
    except DamagedVerdictError as exc:
        _refuse(args.action, str(exc))
        return EXIT_CORRUPT
    except _REFUSED as exc:
        reason = (
            "the rater tag or rubric is not of an allowed form"
            if args.action == "rate" and isinstance(exc, psycopg.errors.CheckViolation)
            else str(exc)
        )
        return _refuse(args.action, reason)
    for line in lines:
        _say(line)
    return 0


def _queue(conn: Connection, args: argparse.Namespace, settings: Settings) -> list[str]:
    del args, settings
    return queue_lines(unrated_receptions(conn))


def _apply(conn: Connection, args: argparse.Namespace, settings: Settings) -> list[str]:
    model = load_verdict_model(args.model)
    now = datetime.now(UTC)
    report = apply_verdicts(conn, registry_for(conn, settings, now), model, now=now)
    conn.commit()
    return apply_lines(report)


def apply_lines(report: VerdictBuildReport) -> list[str]:
    """One application's outcome, as printed."""
    routes = " · ".join(f"{name} {n}" for name, n in report.routes.items()) or "none"
    return [
        f"verdicts by {report.method}",
        f"  scored             {report.scored} ({report.simulated} simulated)",
        f"  written            {report.written}",
        f"  routes             {routes}",
    ]


def _rate(conn: Connection, args: argparse.Namespace, settings: Settings) -> list[str]:
    del settings
    row = insert_rating(
        conn,
        NewRating(
            assignment_id=args.assignment,
            revision=args.revision,
            usable=args.usable,
            rubric=args.rubric,
            rater=args.rater,
        ),
    )
    conn.commit()
    answer = "usable" if args.usable else "unusable"
    return [f"rated {args.assignment} revision {args.revision} {answer} (rating {row})"]


def queue_lines(queued: list[QueuedReception]) -> list[str]:
    """The queue as printed: one block per reception, its products beneath."""
    if not queued:
        return ["nothing to rate: every measured reception with products is rated"]
    lines = [f"{len(queued)} receptions to rate, oldest first"]
    for one in queued:
        lines.append(
            f"{one.assignment_id}  revision {one.revision}  {one.station_id}"
            f"  {one.satellite_id}  {one.started_at.isoformat()}"
        )
        lines.extend(
            f"    {product.kind}  {product.sha256.hex()}  {product.uri or '-'}"
            for product in one.products
        )
    return lines


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian verdict {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

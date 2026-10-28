"""``meridian notify`` — owner reports: queue, send, list, check, regenerate.

``queue`` queues every report now due, as the jobs service's ``reports`` task
does each round. ``send`` tries every report due once; with ``--follow`` it is
the ``notifications`` service, the only process that holds a recipient or a
channel's secret (D-304). ``list`` prints recent reports, each with its state
and its last error class. ``test`` sends one fixed message by a channel to one
key of the recipients file, which checks a channel before any report is due.
``regenerate`` renders a delivered report again from the rows it names and
says whether it matches the hashes recorded when it was queued (D-302).

No line this command prints names a recipient (D-300).

Reference: docs/DECISIONS.md D-098, D-300, D-302, D-304, D-305.
"""

from __future__ import annotations

import argparse
import logging.config
import signal
import sys
import threading
from argparse import Namespace
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.cli_serve import logging_configuration
from meridian.config import Settings, load_settings
from meridian.config_checks import DATABASE_PASSWORD
from meridian.notifications.channels import DeliveryError, Outgoing
from meridian.notifications.compose import deployment_of, queue_due
from meridian.notifications.config import (
    CHANNELS,
    NotificationsConfig,
    NotificationsConfigError,
    load_deployed_notifications_config,
)
from meridian.notifications.recipients import load_deployed_recipients
from meridian.notifications.send import (
    channels_from_environment,
    regenerate,
    send_due,
)
from meridian.notifications.templates import Rendered
from meridian.reliability.config import (
    ReliabilityConfigError,
    load_deployed_reliability_config,
)
from meridian.secret_files import InsecureConfigurationError
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.report_deliveries import (
    StoredDelivery,
    find_deliveries,
    find_delivery,
)
from meridian.store.stations import Connection

__all__ = ["add_notify_parser", "delivery_lines", "run_notify"]

EXIT_FAILED = 1
NOTIFY_SECRETS = frozenset({DATABASE_PASSWORD})
"""The secrets the sender answers for at start; its channels' are its own."""

_REFUSED = (
    NotificationsConfigError,
    ReliabilityConfigError,
    InsecureConfigurationError,
    DatabaseUnreachableError,
    OSError,
    psycopg.Error,
)


def add_notify_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian notify`` and its actions."""
    notify = subcommands.add_parser(
        "notify",
        help="owner reports: queue, send, list, test a channel, regenerate",
        description=(
            "Plain-language reports to the owners of team-operated stations, "
            "from versioned templates (D-098, D-300)."
        ),
    )
    actions = notify.add_subparsers(dest="action", metavar="<action>")
    actions.add_parser("queue", help="queue every report now due")
    send = actions.add_parser("send", help="send every report now due")
    send.add_argument(
        "--follow", action="store_true", help="keep sending until stopped"
    )
    send.add_argument(
        "--interval",
        type=int,
        default=30,
        help="seconds between passes with --follow (default: 30)",
    )
    listing = actions.add_parser("list", help="print recent reports and their state")
    listing.add_argument("--station", default=None, help="one station's reports")
    listing.add_argument("--limit", type=int, default=20, help="how many (default 20)")
    test = actions.add_parser("test", help="send one fixed message by a channel")
    test.add_argument("--channel", required=True, choices=CHANNELS)
    test.add_argument(
        "--to",
        required=True,
        metavar="KEY",
        help="a key of the recipients file: a station id, or any other name",
    )
    again = actions.add_parser(
        "regenerate", help="render a report again and check it against its hashes"
    )
    again.add_argument("delivery_id", type=int)
    again.add_argument("--print", action="store_true", help="also print its text")


def run_notify(args: argparse.Namespace) -> int:
    """Handle every ``meridian notify`` action."""
    try:
        settings = load_settings(secrets_held=NOTIFY_SECRETS)
        config = load_deployed_notifications_config()
        if args.action in _WITHOUT_A_CONNECTION:
            return _WITHOUT_A_CONNECTION[args.action](settings, config, args)
        with connect_once(settings) as conn:
            return _WITH_A_CONNECTION[args.action](conn, settings, config, args)
    except _REFUSED as exc:
        return _refuse(args.action, str(exc))


def _queue(
    conn: Connection, settings: Settings, config: NotificationsConfig, _: Namespace
) -> int:
    deployment = deployment_of(
        load_deployed_reliability_config(), _verdict_method(settings)
    )
    queued = queue_due(conn, config, deployment, datetime.now(UTC))
    _say(f"queued {queued.passes} pass and {queued.weeks} weekly reports")
    return 0


def _list(
    conn: Connection, _settings: Settings, _config: NotificationsConfig, args: Namespace
) -> int:
    rows = find_deliveries(conn, station_id=args.station, limit=args.limit)
    for line in delivery_lines(rows):
        _say(line)
    return 0


def delivery_lines(rows: list[StoredDelivery]) -> list[str]:
    """One line per report: what it is about, where it went, and how it went."""
    if not rows:
        return ["no reports"]
    lines = []
    for row in rows:
        subject = (
            row.assignment_id
            if row.kind == "pass"
            else f"week of {row.period_start:%Y-%m-%d}"
        )
        label = " (simulated)" if row.simulated else ""
        error = f", last error {row.last_error}" if row.last_error else ""
        lines.append(
            f"{row.id} {row.kind} {subject} {row.station_id}{label} by {row.channel}:"
            f" {row.state} after {row.attempts} attempts{error}"
            f"  [{row.template_version}, content {row.content_sha256.hex()[:12]}]"
        )
    return lines


def _send(settings: Settings, config: NotificationsConfig, args: Namespace) -> int:
    logging.config.dictConfig(
        logging_configuration(settings.api_log_level.strip().lower())
    )
    channels = channels_from_environment(config)
    recipients = load_deployed_recipients()

    def one_pass() -> None:
        with connect_once(settings) as conn:
            conn.autocommit = True
            report = send_due(
                conn,
                retry=config.retry,
                channels=channels,
                recipients=recipients,
                now=datetime.now(UTC),
            )
        if not args.follow:
            _say(
                f"attempted {report.attempted}: {report.sent} sent,"
                f" {report.retrying} to retry, {report.abandoned} abandoned"
            )

    if not args.follow:
        one_pass()
        return 0
    stop = threading.Event()
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda _signal, _frame: stop.set())
    while not stop.is_set():
        try:
            one_pass()
        except (DatabaseUnreachableError, psycopg.Error):
            logging.getLogger(__name__).warning(
                "the database did not answer; the next pass will try again"
            )
        stop.wait(max(1, args.interval))
    return 0


def _test(_settings: Settings, config: NotificationsConfig, args: Namespace) -> int:
    channel = channels_from_environment(config).get(args.channel)
    if channel is None:
        return _refuse("test", f"the {args.channel} channel has no secret or settings")
    recipient = load_deployed_recipients().get(args.to)
    if recipient is None and args.channel != "sink":
        return _refuse("test", f"the recipients file has no key {args.to}")
    rendered = Rendered(
        subject="Meridian: a test of this channel",
        body=(
            f"This is a test message from Meridian, sent by the {args.channel}"
            " channel. It is not a report and says nothing about any station."
        ),
    )
    try:
        channel.send(Outgoing(0, rendered), recipient or "")
    except DeliveryError as failure:
        return _refuse("test", f"not sent: {failure.error_class}")
    _say(f"sent a test message by {args.channel} to {args.to}")
    return 0


def _regenerate(
    conn: Connection, _settings: Settings, _config: NotificationsConfig, args: Namespace
) -> int:
    report = find_delivery(conn, args.delivery_id)
    if report is None:
        return _refuse("regenerate", f"no report {args.delivery_id}")
    try:
        again = regenerate(conn, report)
    except (LookupError, KeyError, TypeError, ValueError) as exc:
        return _refuse("regenerate", f"report {report.id} cannot be read: {exc}")
    if args.print:
        _say(again.rendered.subject)
        _say("")
        _say(again.rendered.body)
        _say("")
    if again.identical:
        _say(
            f"report {report.id} regenerates identically: inputs"
            f" {report.inputs_sha256.hex()[:12]}, content"
            f" {report.content_sha256.hex()[:12]}"
        )
        return 0
    differs = [
        name
        for name, matched in (
            ("inputs", again.inputs_match),
            ("content", again.content_match),
        )
        if not matched
    ]
    return _refuse("regenerate", f"report {report.id} differs: {', '.join(differs)}")


_WITHOUT_A_CONNECTION: dict[
    str, Callable[[Settings, NotificationsConfig, Namespace], int]
] = {"send": _send, "test": _test}
"""The actions that open their own connections, or need none."""

_WITH_A_CONNECTION: dict[
    str, Callable[[Connection, Settings, NotificationsConfig, Namespace], int]
] = {"queue": _queue, "list": _list, "regenerate": _regenerate}


def _verdict_method(settings: Settings) -> str | None:
    if not settings.verdict_model:
        return None
    # Inside the function: only ``queue`` with a model configured needs it.
    from meridian.prediction.verdict_files import load_verdict_model  # noqa: PLC0415

    return load_verdict_model(Path(settings.verdict_model)).method


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian notify {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED

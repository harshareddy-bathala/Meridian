"""Send every report that is due, and record what each attempt came to.

Run by the ``notifications`` service, ``meridian notify send --follow``: the
only process that holds a recipient or a channel's secret (D-304). It reads
reports the jobs service queued and writes attempts, and touches nothing else,
so a delivery that fails leaves every assignment, observation and result as it
was (D-098).

**A report is regenerated before it is sent.** The rows it names are read
again and rendered by its template version, and it goes out only if both
hashes match what was recorded when it was queued; if not, it is abandoned as
``regeneration_mismatch`` and nothing is sent (D-302). What a person receives
is therefore always what ``meridian notify regenerate`` proves.

**Retries back off** (D-305): a failure waits a minute, then twice that each
time up to the cap, and after the configured attempts the report is abandoned.
A failure no retry can help — a refused recipient, a simulated station on a
person's channel — is abandoned at once. **Delivery is at least once**: a crash
between sending and recording sends the report again on restart. An email's
``Message-ID`` is the report's, so the copy is recognisably the same message.

**Nothing logged names a recipient.** A line says the report's id, its
station, its channel and its error class, and never an address or a server's
words (D-300). The channel's own secrets are registered with the redacting
filter as they are read (D-204).

Reference: docs/DECISIONS.md D-098, D-204, D-300, D-302, D-304, D-305.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from meridian.log_redaction import remember_secrets
from meridian.notifications.channels import (
    Channel,
    DeliveryError,
    EmailChannel,
    Outgoing,
    SinkChannel,
    TelegramChannel,
)
from meridian.notifications.config import NotificationsConfig, RetryPolicy
from meridian.notifications.facts import inputs_sha256
from meridian.notifications.reading import facts_for
from meridian.notifications.templates import Rendered, content_sha256, render
from meridian.secret_files import read_previous_secret
from meridian.store.report_deliveries import (
    StoredDelivery,
    due_deliveries,
    record_attempt,
)
from meridian.store.stations import Connection

__all__ = [
    "SEND_BATCH",
    "Regeneration",
    "SendReport",
    "channels_from_environment",
    "regenerate",
    "send_due",
]

_log = logging.getLogger(__name__)

SEND_BATCH = 50
"""Reports one pass of the sender tries at most."""


@dataclass(frozen=True, slots=True)
class Regeneration:
    """A stored report rendered again from the rows it names."""

    rendered: Rendered
    inputs_match: bool
    content_match: bool

    @property
    def identical(self) -> bool:
        """Whether both hashes are the ones recorded when it was queued."""
        return self.inputs_match and self.content_match


def regenerate(conn: Connection, report: StoredDelivery) -> Regeneration:
    """Read a report's rows again and render it by its own template version.

    Raises:
        LookupError: A row the report names is not stored.
        KeyError: No template has the report's version.
        TypeError: The report's inputs are not its kind's.
    """
    facts = facts_for(
        conn,
        kind=report.kind,
        station_id=report.station_id,
        period_start=report.period_start,
        inputs=report.inputs_json,
    )
    rendered = render(report.template_version, facts)
    return Regeneration(
        rendered=rendered,
        inputs_match=inputs_sha256(facts) == report.inputs_sha256,
        content_match=content_sha256(rendered) == report.content_sha256,
    )


@dataclass(frozen=True, slots=True)
class SendReport:
    """What one pass of the sender did."""

    attempted: int
    sent: int
    retrying: int
    abandoned: int


def send_due(  # noqa: PLR0913 — one pass's parts, each by name
    conn: Connection,
    *,
    retry: RetryPolicy,
    channels: Mapping[str, Channel],
    recipients: Mapping[str, str],
    now: datetime,
    limit: int = SEND_BATCH,
) -> SendReport:
    """Try every report now due, oldest first, recording each attempt.

    Each attempt is recorded in a transaction of its own as soon as it is made,
    so a crash costs at most the one report in hand.
    """
    states: Counter[str] = Counter()
    for report in due_deliveries(conn, now, limit=limit):
        failure = _attempt(conn, report, channels, recipients)
        with conn.transaction():
            state = _record(conn, report, failure, retry, now)
        states[state] += 1
        if failure is None:
            _log.info(
                "report %d for %s sent by %s",
                report.id,
                report.station_id,
                report.channel,
            )
        else:
            _log.warning(
                "report %d for %s %s by %s: %s",
                report.id,
                report.station_id,
                "abandoned" if state == "abandoned" else "not sent, will retry",
                report.channel,
                failure.error_class,
            )
    return SendReport(
        attempted=sum(states.values()),
        sent=states["sent"],
        retrying=states["retrying"],
        abandoned=states["abandoned"],
    )


def _attempt(
    conn: Connection,
    report: StoredDelivery,
    channels: Mapping[str, Channel],
    recipients: Mapping[str, str],
) -> DeliveryError | None:
    """Send one report; ``None`` when it was sent, else why it was not."""
    try:
        channel, recipient = _route(report, channels, recipients)
        channel.send(Outgoing(report.id, _checked(conn, report)), recipient)
    except DeliveryError as failure:
        return failure
    return None


def _route(
    report: StoredDelivery,
    channels: Mapping[str, Channel],
    recipients: Mapping[str, str],
) -> tuple[Channel, str]:
    """The channel a report goes by and who it goes to, or why it cannot go."""
    if report.simulated and report.channel != "sink":
        raise DeliveryError("simulated_not_sink", permanent=True)
    channel = channels.get(report.channel)
    if channel is None:
        raise DeliveryError("channel_not_configured")
    recipient = "" if report.channel == "sink" else recipients.get(report.station_id)
    if recipient is None:
        raise DeliveryError("no_recipient")
    return channel, recipient


def _checked(conn: Connection, report: StoredDelivery) -> Rendered:
    """The report rendered again, only if it is exactly what was queued."""
    try:
        again = regenerate(conn, report)
    except LookupError:
        raise DeliveryError("inputs_missing", permanent=True) from None
    except (KeyError, TypeError, ValueError):
        raise DeliveryError("template_unknown", permanent=True) from None
    if not again.identical:
        raise DeliveryError("regeneration_mismatch", permanent=True)
    return again.rendered


def _record(
    conn: Connection,
    report: StoredDelivery,
    failure: DeliveryError | None,
    retry: RetryPolicy,
    now: datetime,
) -> str:
    """Append the attempt, and say what state it leaves the report in."""
    if failure is None:
        record_attempt(conn, report.id, attempted_at=now, outcome="sent")
        return "sent"
    tries = report.attempts + 1
    if failure.permanent or tries >= retry.attempts:
        record_attempt(
            conn,
            report.id,
            attempted_at=now,
            outcome="abandoned",
            error_class=failure.error_class,
        )
        return "abandoned"
    record_attempt(
        conn,
        report.id,
        attempted_at=now,
        outcome="failed",
        error_class=failure.error_class,
        next_attempt_at=now + timedelta(seconds=retry.wait_after(tries)),
    )
    return "retrying"


def channels_from_environment(config: NotificationsConfig) -> dict[str, Channel]:
    """Every channel this process can send by, with its secrets read from files.

    The sink always. Email when ``[email]`` names a host, with the password
    from ``SMTP_PASSWORD_FILE``; Telegram when ``TELEGRAM_BOT_TOKEN_FILE``
    holds a token. A channel whose file is empty is absent, and a report for it
    is retried as ``channel_not_configured`` until one is given.

    Raises:
        InsecureConfigurationError: A named secret file cannot be read.
    """
    timeout_s = config.retry.timeout_s
    channels: dict[str, Channel] = {"sink": SinkChannel(Path(config.sink_directory))}
    # An empty file is a channel not set up, as an empty previous pepper is a
    # rotation retired (D-201); an unreadable one is still refused.
    password = read_previous_secret("SMTP_PASSWORD")
    token = read_previous_secret("TELEGRAM_BOT_TOKEN")
    remember_secrets([password, token])
    if config.email.host:
        channels["email"] = EmailChannel(config.email, password, timeout_s)
    if token:
        channels["telegram"] = TelegramChannel(config.telegram, token, timeout_s)
    return channels

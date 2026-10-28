"""Sending owner reports — what each attempt records, and what it never touches.

Marked ``integration`` by the directory hook. What only a database shows:
- a report sent to the sink is its exact bytes, and is never sent again;
- a failure is retried after a minute, then after twice that, and abandoned
  after the configured attempts; one no retry can help is abandoned at once;
- a report that no longer regenerates to its hashes is never sent (D-302);
- a report with no recipient, or for a channel with no secret, waits;
- a failed delivery leaves assignments, observations, verdicts, diagnoses and
  classifications exactly as they were (D-098);
- no log line names a recipient (D-300).

Reference: docs/DECISIONS.md D-098, D-300, D-302, D-305.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.notifications.channels import (  # noqa: E402
    DeliveryError,
    EmailChannel,
    Outgoing,
    SinkChannel,
)
from meridian.notifications.compose import Deployment, queue_due  # noqa: E402
from meridian.notifications.config import (  # noqa: E402
    EmailSettings,
    NotificationsConfig,
    Recipient,
    RetryPolicy,
)
from meridian.notifications.send import send_due  # noqa: E402
from meridian.store.report_deliveries import (  # noqa: E402
    StoredDelivery,
    find_attempts,
    find_deliveries,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 11, 30, tzinfo=UTC)
DEPLOYMENT = Deployment(
    classification_method="classification-1",
    classification_sha256=bytes([5]) * 32,
    diagnosis_method="diagnosis-1",
    diagnosis_sha256=bytes([6]) * 32,
    verdict_method="verdict-1:abcdefabcdef",
)
OWNER = "owner-private@example.org"
RETRY = RetryPolicy(first_s=60, cap_s=3_600, attempts=3, timeout_s=2)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@dataclass
class _Fake:
    """A channel that records what it was handed, and fails as told."""

    failure: DeliveryError | None = None
    sent: list[tuple[int, str]] = field(default_factory=list)

    def send(self, outgoing: Outgoing, recipient: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append((outgoing.delivery_id, recipient))


def queued(world: Any, name: str, channel: str = "email") -> StoredDelivery:
    """One decoded pass with its verdict, reported on ``channel`` and queued."""
    station = world.station(f"st-{name}")
    subject = world.assignment(station, f"a-{name}", state="reported")
    world.rows.observation(subject)
    world.classified(subject, "successful_reception")
    world.verdict(subject, 0.9)
    config = NotificationsConfig(stations=(Recipient(station, channel, weekly=False),))
    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 1
    [report] = find_deliveries(world.conn, station_id=station)
    return report


def send(world: Any, channels: dict[str, Any], at: datetime, **recipients: str) -> Any:
    return send_due(
        world.conn,
        retry=RETRY,
        channels=channels,
        recipients=recipients,
        now=at,
    )


def outcomes(world: Any, report: StoredDelivery) -> list[tuple[str, str | None]]:
    return [
        (one.outcome, one.error_class) for one in find_attempts(world.conn, report.id)
    ]


def test_a_report_goes_to_the_sink_byte_for_byte_and_only_once(
    world: Any, tmp_path: Path
) -> None:
    report = queued(world, "sink", channel="sink")
    sink = SinkChannel(tmp_path)

    first = send(world, {"sink": sink}, NOW)
    again = send(world, {"sink": sink}, NOW + timedelta(days=1))

    assert (first.sent, again.attempted) == (1, 0)
    written = sink.path_of(report.id).read_bytes()
    assert hashlib.sha256(written).digest() == report.content_sha256
    assert outcomes(world, report) == [("sent", None)]


def test_a_failure_backs_off_and_is_abandoned_after_its_attempts(world: Any) -> None:
    report = queued(world, "backoff")
    failing = _Fake(failure=DeliveryError("timeout"))
    channels = {"email": failing}

    assert send(world, channels, NOW, **{report.station_id: OWNER}).retrying == 1
    early = NOW + timedelta(seconds=59)
    assert send(world, channels, early, **{report.station_id: OWNER}).attempted == 0
    second = NOW + timedelta(seconds=60)
    assert send(world, channels, second, **{report.station_id: OWNER}).retrying == 1
    third = second + timedelta(seconds=120)
    assert send(world, channels, third, **{report.station_id: OWNER}).abandoned == 1

    attempts = find_attempts(world.conn, report.id)
    assert [one.outcome for one in attempts] == ["failed", "failed", "abandoned"]
    assert [one.next_attempt_at for one in attempts[:2]] == [second, third]
    assert send(world, channels, third + timedelta(days=9)).attempted == 0


def test_a_refusal_no_retry_can_help_is_abandoned_at_once(world: Any) -> None:
    report = queued(world, "refused")
    refused = _Fake(failure=DeliveryError("refused", permanent=True))

    send(world, {"email": refused}, NOW, **{report.station_id: OWNER})

    assert outcomes(world, report) == [("abandoned", "refused")]


def test_a_report_that_no_longer_regenerates_is_never_sent(world: Any) -> None:
    report = queued(world, "mismatch")
    world.conn.execute(
        "update report_deliveries set content_sha256 = %s where id = %s",
        (bytes(32), report.id),
    )
    channel = _Fake()

    send(world, {"email": channel}, NOW, **{report.station_id: OWNER})

    assert channel.sent == []
    assert outcomes(world, report) == [("abandoned", "regeneration_mismatch")]


def test_a_report_waits_for_its_recipient_and_its_channel(world: Any) -> None:
    report = queued(world, "waiting")
    channel = _Fake()

    send(world, {"email": channel}, NOW)
    send(world, {}, NOW + timedelta(minutes=1), **{report.station_id: OWNER})
    send(
        world,
        {"email": channel},
        NOW + timedelta(minutes=3),
        **{report.station_id: OWNER},
    )

    assert outcomes(world, report) == [
        ("failed", "no_recipient"),
        ("failed", "channel_not_configured"),
        ("sent", None),
    ]
    assert channel.sent == [(report.id, OWNER)]


_STORED = (
    "assignments",
    "observations",
    "reception_verdicts",
    "loss_diagnoses",
    "pass_classifications",
)


def _fingerprint(conn: Any) -> dict[str, str]:
    return {
        table: conn.execute(
            f"select md5(coalesce(string_agg(t::text, '|' order by t::text), ''))"
            f" from {table} t"
        ).fetchone()[0]
        for table in _STORED
    }


def test_a_failed_delivery_leaves_every_stored_result_untouched(world: Any) -> None:
    report = queued(world, "untouched")
    before = _fingerprint(world.conn)
    failing = _Fake(failure=DeliveryError("unreachable"))

    for minutes in (0, 1, 3):
        send(
            world,
            {"email": failing},
            NOW + timedelta(minutes=minutes),
            **{report.station_id: OWNER},
        )

    assert _fingerprint(world.conn) == before
    assert len(find_attempts(world.conn, report.id)) == 3


def test_no_log_line_names_the_recipient(
    world: Any, caplog: pytest.LogCaptureFixture
) -> None:
    sent = queued(world, "log-sent")
    refused = queued(world, "log-refused")
    unreachable = queued(world, "log-unreachable")
    closed = EmailChannel(
        EmailSettings(
            host="127.0.0.1", port=9, from_address="hello@example.org", starttls=False
        ),
        "smtp-password",
        1,
    )
    recipients = {one.station_id: OWNER for one in (sent, refused, unreachable)}

    with caplog.at_level(logging.DEBUG):
        send_due(
            world.conn,
            retry=RETRY,
            channels={"email": _Fake()},
            recipients={sent.station_id: OWNER},
            now=NOW,
        )
        world.conn.execute(
            "update report_deliveries set channel = 'telegram' where id = %s",
            (refused.id,),
        )
        send_due(
            world.conn,
            retry=RETRY,
            channels={
                "email": closed,
                "telegram": _Fake(failure=DeliveryError("http_403", permanent=True)),
            },
            recipients=recipients,
            now=NOW + timedelta(minutes=5),
        )

    text = caplog.text
    assert OWNER not in text
    assert all(one.station_id in text for one in (sent, refused, unreachable))
    assert "unreachable" in text
    assert "http_403" in text

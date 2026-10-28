"""Queue every report that is due: render it, hash it, and write it once.

Run by the jobs service's ``reports`` task after the watch, and by
``meridian notify queue``. **It reads and writes the database and nothing
else**: no recipient, no channel's secret and no network, so the jobs process
keeps only what it held before (D-206, D-304). Sending is
:mod:`meridian.notifications.send`, in a process of its own.

A report is rendered here so that its hash is fixed when it is queued; the
sender renders it again and sends only if the text is the same (D-302).

* **A pass** is due when :func:`~meridian.store.report_reads.find_due_passes`
  says so: classified, and either settled or past the wait.
* **A week** is the latest one, Monday to Monday UTC, that ended at least the
  settle margin ago (D-306). Only that week is queued: a station listed today
  gets this week's report, not one for every week it has existed.

Reference: docs/DECISIONS.md D-206, D-302, D-304, D-306.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from meridian.notifications.config import NotificationsConfig, check_stations
from meridian.notifications.facts import inputs_sha256
from meridian.notifications.reading import (
    WEEK,
    pass_facts,
    pass_inputs,
    week_facts,
    week_inputs,
)
from meridian.notifications.templates import CURRENT, content_sha256, render
from meridian.reliability import classification, diagnosis
from meridian.reliability.config import ReliabilityConfig
from meridian.store.report_deliveries import (
    NewPassReport,
    NewWeeklyReport,
    queue_pass_report,
    queue_weekly_report,
)
from meridian.store.report_reads import find_due_passes
from meridian.store.stations import Connection
from meridian.store.week_reads import (
    find_queued_weeks,
    find_week_receptions,
    find_week_warnings,
)

__all__ = [
    "PASS_BATCH",
    "Deployment",
    "QueueReport",
    "deployment_of",
    "last_week",
    "queue_due",
]

PASS_BATCH = 200
"""Pass reports one round queues at most; the rest wait for the next round."""


@dataclass(frozen=True, slots=True)
class Deployment:
    """Which classification, diagnosis and verdict a report reads."""

    classification_method: str
    classification_sha256: bytes
    diagnosis_method: str
    diagnosis_sha256: bytes
    verdict_method: str | None


def deployment_of(config: ReliabilityConfig, verdict_method: str | None) -> Deployment:
    """What the deployment's reliability file and verdict model decide."""
    return Deployment(
        classification_method=classification.METHOD,
        classification_sha256=config.classification.sha256(),
        diagnosis_method=diagnosis.METHOD,
        diagnosis_sha256=config.diagnosis.sha256(),
        verdict_method=verdict_method,
    )


@dataclass(frozen=True, slots=True)
class QueueReport:
    """What one round queued."""

    passes: int
    weeks: int


def last_week(now: datetime, settle_s: int) -> datetime:
    """The start of the latest week that ended at least ``settle_s`` before ``now``."""
    settled = (now - timedelta(seconds=settle_s)).astimezone(UTC)
    midnight = settled.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=settled.weekday()) - WEEK


def queue_due(
    conn: Connection,
    config: NotificationsConfig,
    deployment: Deployment,
    now: datetime,
    *,
    limit: int = PASS_BATCH,
) -> QueueReport:
    """Queue every pass and week now due for the stations ``config`` lists.

    Raises:
        NotificationsConfigError: The file names a station the registry does
            not hold, or a simulated station on a channel other than the sink.
    """
    check_stations(conn, config)
    return QueueReport(
        passes=_queue_passes(conn, config, deployment, now, limit),
        weeks=_queue_weeks(conn, config, deployment, now),
    )


def _queue_passes(
    conn: Connection,
    config: NotificationsConfig,
    deployment: Deployment,
    now: datetime,
    limit: int,
) -> int:
    due = find_due_passes(
        conn,
        config.channels(),
        now,
        wait_s=config.wait_s,
        classification_method=deployment.classification_method,
        classification_sha256=deployment.classification_sha256,
        diagnosis_method=deployment.diagnosis_method,
        diagnosis_sha256=deployment.diagnosis_sha256,
        verdict_method=deployment.verdict_method,
        limit=limit,
    )
    queued = 0
    version = CURRENT["pass"]
    for one in due:
        inputs = pass_inputs(one.key)
        facts = pass_facts(conn, inputs)
        written = queue_pass_report(
            conn,
            NewPassReport(
                assignment_id=one.key.assignment_id,
                channel=one.channel,
                template_version=version,
                inputs=inputs,
                inputs_sha256=inputs_sha256(facts),
                content_sha256=content_sha256(render(version, facts)),
            ),
        )
        queued += written is not None
    return queued


def _queue_weeks(
    conn: Connection,
    config: NotificationsConfig,
    deployment: Deployment,
    now: datetime,
) -> int:
    channels = config.channels(weekly=True)
    start = last_week(now, config.weekly_settle_s)
    held = find_queued_weeks(conn, channels, start)
    queued = 0
    version = CURRENT["weekly"]
    for station_id, channel in sorted(channels.items()):
        if station_id in held:
            continue
        keys = find_week_receptions(
            conn,
            station_id,
            start,
            start + WEEK,
            classification_method=deployment.classification_method,
            classification_sha256=deployment.classification_sha256,
            diagnosis_method=deployment.diagnosis_method,
            diagnosis_sha256=deployment.diagnosis_sha256,
            verdict_method=deployment.verdict_method,
        )
        inputs = week_inputs(keys, find_week_warnings(conn, station_id, start + WEEK))
        facts = week_facts(conn, station_id, start, inputs)
        written = queue_weekly_report(
            conn,
            NewWeeklyReport(
                station_id=station_id,
                period_start=start,
                channel=channel,
                template_version=version,
                inputs=inputs,
                inputs_sha256=inputs_sha256(facts),
                content_sha256=content_sha256(render(version, facts)),
            ),
        )
        queued += written is not None
    return queued

"""The reports half of a round: queue every owner report that is now due.

Runs last in a round, after the diagnosis and the watch whose results a report
says. It reads and writes the database alone: no recipient, no channel secret
and no network, so the jobs process holds only what it held before (D-206).
Sending is the ``notifications`` service's, in a process of its own (D-304).

A deployment that lists no station queues nothing and still succeeds, so the
task's metrics say it ran. A notifications file the registry disagrees with —
an unregistered station, or a simulated one on a channel other than the sink —
fails the task every round until it is fixed, and the task's alert says so.

Reference: docs/DECISIONS.md D-206, D-300, D-304.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from meridian.jobs.job_metrics import REPORTS
from meridian.jobs.rounds import _timed
from meridian.notifications.compose import Deployment, QueueReport, queue_due
from meridian.notifications.config import NotificationsConfig
from meridian.store.stations import Connection

__all__ = ["DatabaseReportWork", "ReportWork", "run_report_round"]

_log = logging.getLogger(__name__)


class ReportWork(Protocol):
    """The reports task; a stub stands in for it in tests."""

    def queue(self, now: datetime) -> QueueReport:
        """Queue every report now due."""
        ...


class DatabaseReportWork:
    """The real task: opens its own connection, commits and closes it."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection]],
        *,
        config: NotificationsConfig,
        deployment: Deployment,
    ) -> None:
        """Bind the task to a way of connecting and what it reports on.

        Args:
            connect: Returns a connection that commits on a clean exit.
            config: The deployment's notifications file.
            deployment: Which classification, diagnosis and verdict to read.
        """
        self._connect = connect
        self._config = config
        self._deployment = deployment

    def queue(self, now: datetime) -> QueueReport:
        """Queue reports exactly as ``meridian notify queue`` does."""
        if not self._config.stations:
            return QueueReport(passes=0, weeks=0)
        with self._connect() as conn:
            return queue_due(conn, self._config, self._deployment, now)


def run_report_round(work: ReportWork, now: datetime) -> QueueReport | None:
    """Queue this round's reports; a failure is survived.

    Returns:
        The report, or ``None`` when the task failed.
    """
    found = _timed(REPORTS, lambda: work.queue(now))
    if found is not None and (found.passes or found.weeks):
        _log.info(
            "owner reports queued: %d passes, %d weeks", found.passes, found.weeks
        )
    return found

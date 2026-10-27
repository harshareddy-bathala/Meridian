"""The watch half of a round: train baselines that are due, judge every chain.

Runs last in a round, after the diagnosis whose causes a baseline leaves out
(D-110, D-293). Kept apart from :mod:`meridian.jobs.rounds` for the reason the
diagnosis half is: it needs the orbit service and the reliability settings,
which scheduling does not.

A round that finds no chain to judge still succeeds. A warning raised here
carries the round's instant as ``raised_at``, which is when it could first be
seen (D-292).

Reference: docs/DECISIONS.md D-110, D-292 to D-294.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from meridian.jobs.job_metrics import WATCH
from meridian.jobs.rounds import _timed
from meridian.orbit.service import OrbitService
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.watch_run import WatchRunReport, watch_chains
from meridian.store.stations import Connection

__all__ = ["DatabaseWatchWork", "WatchWork", "run_watch_round"]

_log = logging.getLogger(__name__)


class WatchWork(Protocol):
    """The watch task; a stub stands in for it in tests."""

    def watch(self, now: datetime) -> WatchRunReport:
        """Train what is due, and judge every chain."""
        ...


class DatabaseWatchWork:
    """The real task: opens its own connection, commits and closes it."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection]],
        orbit: OrbitService,
        *,
        config: ReliabilityConfig,
    ) -> None:
        """Bind the task to a way of connecting, the orbit service and settings.

        Args:
            connect: Returns a connection that commits on a clean exit.
            orbit: Places samples in a station's sky.
            config: The deployment's reliability configuration.
        """
        self._connect = connect
        self._orbit = orbit
        self._config = config

    def watch(self, now: datetime) -> WatchRunReport:
        """Run the watch exactly as ``meridian watch run`` does."""
        with self._connect() as conn:
            return watch_chains(conn, self._orbit, config=self._config, now=now)


def run_watch_round(work: WatchWork, now: datetime) -> WatchRunReport | None:
    """Watch every chain this round; a failure is survived.

    Returns:
        The report, or ``None`` when the task failed.
    """
    found = _timed(WATCH, lambda: work.watch(now))
    if found is not None and (found.raised or found.cleared):
        _log.warning(
            "receive-chain watch by %s: raised %s, cleared %s",
            found.method,
            list(found.raised),
            list(found.cleared),
        )
    return found

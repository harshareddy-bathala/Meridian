"""The reliability half of a round: expire untaken work, then classify.

Runs after pass generation and scheduling, in the same process and on the same
timer (D-110). Kept apart from :mod:`meridian.jobs.rounds` because it needs the
registry and the reliability configuration, which scheduling does not.

* **The sweep** expires every ``issued`` assignment whose window closed untaken,
  whatever its station did or did not send (D-183).
* **The classification** classifies scheduled passes that have settled and
  stores each with its evidence (D-182), at most :data:`CLASSIFY_BATCH` a round,
  oldest first, so the first round over a long history is many short
  transactions rather than one long one. A pass settles a day after its
  window, so most rounds find nothing new; a round that finds nothing still
  succeeds.

Each is supervised as the scheduling tasks are: timed, and a failure logged and
counted, never fatal to the loop.

Reference: docs/DECISIONS.md D-110, D-182, D-183.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meridian.jobs.job_metrics import EXPIRY_SWEEP, RELIABILITY
from meridian.jobs.rounds import _timed
from meridian.registry import Registry
from meridian.reliability.accounting import AccountingReport, classify_settled
from meridian.reliability.config import ClassificationConfig
from meridian.store.assignment_expiry import expire_untaken_assignments
from meridian.store.stations import Connection

__all__ = [
    "CLASSIFY_BATCH",
    "DatabaseReliabilityWork",
    "ReliabilityOutcome",
    "ReliabilityWork",
    "run_reliability_round",
]

_log = logging.getLogger(__name__)

CLASSIFY_BATCH = 500
"""Passes one round classifies at most. Fifty stations at about six passes a
day settle some three hundred a day, so a round keeps up with a margin while
a backlog drains over successive rounds."""


class ReliabilityWork(Protocol):
    """The two reliability tasks; a stub stands in for them in tests."""

    def sweep(self, now: datetime) -> int:
        """Expire untaken work whose window closed before ``now``."""
        ...

    def classify(self, now: datetime) -> AccountingReport:
        """Classify every pass settled by ``now``."""
        ...


class DatabaseReliabilityWork:
    """The real tasks: each opens its own connection, commits and closes it."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection]],
        registry_for: Callable[[Connection, datetime], Registry],
        config: ClassificationConfig,
    ) -> None:
        """Bind the tasks to a way of connecting, the registry and the margins.

        Args:
            connect: Returns a connection that commits on a clean exit.
            registry_for: Builds the registry over a connection at an instant.
            config: The margins every classification is made under.
        """
        self._connect = connect
        self._registry_for = registry_for
        self._config = config

    def sweep(self, now: datetime) -> int:
        """Run the sweep exactly as ``meridian reliability sweep`` does."""
        with self._connect() as conn:
            return expire_untaken_assignments(conn, now=now)

    def classify(self, now: datetime) -> AccountingReport:
        """Run the accounting exactly as ``meridian reliability classify`` does."""
        with self._connect() as conn:
            return classify_settled(
                conn,
                self._registry_for(conn, now),
                now=now,
                config=self._config,
                limit=CLASSIFY_BATCH,
            )


@dataclass(frozen=True, slots=True)
class ReliabilityOutcome:
    """Which reliability tasks of one round completed."""

    expired: int | None
    classified: AccountingReport | None


def run_reliability_round(work: ReliabilityWork, now: datetime) -> ReliabilityOutcome:
    """Sweep, then classify; a failure of either is recorded and survived.

    The classification runs even if the sweep failed, because it sweeps
    first itself, in its own transaction: a pass is never classified while an
    assignment nobody took is still ``issued`` (D-183).
    """
    expired = _timed(EXPIRY_SWEEP, lambda: work.sweep(now))
    if expired is not None:
        _log.info("expired %d untaken assignments", expired)
    classified = _timed(RELIABILITY, lambda: work.classify(now))
    if classified is not None:
        _log.info(
            "classified %d settled passes, %d rows written, %d left for later",
            classified.classified,
            classified.written,
            classified.deferred,
        )
    return ReliabilityOutcome(expired=expired, classified=classified)

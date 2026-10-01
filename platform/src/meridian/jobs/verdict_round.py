"""The verdict half of a round: score every closed reception the model has not.

Runs after the reliability half, in the same process and on the same timer
(D-110), when ``VERDICT_MODEL`` names a fitted verdict model (D-263). It is
kept apart from :mod:`meridian.jobs.rounds` for the reason the reliability half
is: it needs the registry, which scheduling does not.

At most :data:`VERDICT_BATCH` receptions a round, oldest first, so the first
round after a model is configured drains a long history in short transactions.
A round that finds nothing to score still succeeds. With no model configured
the task is not run and records nothing, so its absence on the metrics says
"not configured" rather than "failing".

Reference: docs/DECISIONS.md D-110, D-263.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from meridian.jobs.job_metrics import VERDICTS
from meridian.jobs.rounds import _timed
from meridian.prediction.verdict_score import VerdictModel
from meridian.store.stations import Connection
from meridian.verdict_build import Listening, VerdictBuildReport, apply_verdicts

__all__ = [
    "VERDICT_BATCH",
    "DatabaseVerdictWork",
    "VerdictWork",
    "run_verdict_round",
]

_log = logging.getLogger(__name__)

VERDICT_BATCH = 500
"""Receptions one round scores at most: the classification's batch, for the
same reason (``reliability_round.CLASSIFY_BATCH``)."""


class VerdictWork(Protocol):
    """The verdict task; a stub stands in for it in tests."""

    def apply(self, now: datetime) -> VerdictBuildReport:
        """Score every closed reception the model has not, up to the batch."""
        ...


class DatabaseVerdictWork:
    """The real task: opens its own connection, commits and closes it."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection]],
        registry_for: Callable[[Connection, datetime], Listening],
        model: VerdictModel,
    ) -> None:
        """Bind the task to a way of connecting, the registry and the model.

        Args:
            connect: Returns a connection that commits on a clean exit.
            registry_for: Builds the registry over a connection at an instant.
            model: The verdict model, read once when the process started.
        """
        self._connect = connect
        self._registry_for = registry_for
        self._model = model

    def apply(self, now: datetime) -> VerdictBuildReport:
        """Run the writer exactly as ``meridian verdict apply`` does, batched."""
        with self._connect() as conn:
            return apply_verdicts(
                conn,
                self._registry_for(conn, now),
                self._model,
                now=now,
                limit=VERDICT_BATCH,
            )


def run_verdict_round(
    work: VerdictWork | None, now: datetime
) -> VerdictBuildReport | None:
    """Apply the verdict model, if one is configured; a failure is survived.

    Returns:
        The report, or ``None`` when no model is configured or the task failed.
    """
    if work is None:
        return None
    applied = _timed(VERDICTS, lambda: work.apply(now))
    if applied is not None and applied.scored:
        _log.info(
            "verdicts by %s: %d scored, %d written",
            applied.method,
            applied.scored,
            applied.written,
        )
    return applied

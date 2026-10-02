"""The diagnosis half of a round: diagnose every classified loss not yet diagnosed.

Runs last in a round, after the classification it reads and the verdicts a
partial reception is read against (D-110, D-272). It is kept apart from
:mod:`meridian.jobs.rounds` for the reason the reliability half is: it needs the
registry, which scheduling does not.

At most :data:`DIAGNOSIS_BATCH` losses a round, oldest first, so the first round
over a long history drains it in short transactions. A round that finds nothing
to diagnose still succeeds. Unlike the verdict task it always runs: a loss can
be diagnosed without a verdict model, and only a partial decode needs one.

Reference: docs/DECISIONS.md D-110, D-272.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from meridian.jobs.job_metrics import DIAGNOSIS
from meridian.jobs.rounds import _timed
from meridian.orbit.service import OrbitService
from meridian.registry import Registry
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis_run import DiagnosisRunReport, diagnose_settled
from meridian.store.stations import Connection

__all__ = [
    "DIAGNOSIS_BATCH",
    "DatabaseDiagnosisWork",
    "DiagnosisWork",
    "run_diagnosis_round",
]

_log = logging.getLogger(__name__)

DIAGNOSIS_BATCH = 500
"""Losses one round diagnoses at most: the classification's batch, for the same
reason (``reliability_round.CLASSIFY_BATCH``)."""


class DiagnosisWork(Protocol):
    """The diagnosis task; a stub stands in for it in tests."""

    def diagnose(self, now: datetime) -> DiagnosisRunReport:
        """Diagnose every classified loss not yet diagnosed, up to the batch."""
        ...


class DatabaseDiagnosisWork:
    """The real task: opens its own connection, commits and closes it."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[Connection]],
        registry_for: Callable[[Connection, datetime], Registry],
        orbit: OrbitService,
        *,
        config: ReliabilityConfig,
        verdict_method: str | None,
    ) -> None:
        """Bind the task to a way of connecting, the registry and its settings.

        Args:
            connect: Returns a connection that commits on a clean exit.
            registry_for: Builds the registry over a connection at an instant.
            orbit: Places samples in a station's sky.
            config: The deployment's reliability configuration.
            verdict_method: The verdict model's method, or ``None`` with none.
        """
        self._connect = connect
        self._registry_for = registry_for
        self._orbit = orbit
        self._config = config
        self._verdict_method = verdict_method

    def diagnose(self, now: datetime) -> DiagnosisRunReport:
        """Run the diagnosis exactly as ``meridian diagnosis run`` does, batched."""
        with self._connect() as conn:
            return diagnose_settled(
                conn,
                self._registry_for(conn, now),
                self._orbit,
                config=self._config,
                verdict_method=self._verdict_method,
                limit=DIAGNOSIS_BATCH,
            )


def run_diagnosis_round(
    work: DiagnosisWork, now: datetime
) -> DiagnosisRunReport | None:
    """Diagnose this round's losses; a failure is survived.

    Returns:
        The report, or ``None`` when the task failed.
    """
    found = _timed(DIAGNOSIS, lambda: work.diagnose(now))
    if found is not None and found.diagnosed:
        _log.info(
            "diagnoses by %s: %d diagnosed, %d written",
            found.method,
            found.diagnosed,
            found.written,
        )
    return found

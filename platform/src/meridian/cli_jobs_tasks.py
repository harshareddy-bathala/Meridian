"""The tasks a jobs round runs after scheduling, each built as configured.

Stage 20's sweep and classification, Stage 26's verdicts, Stage 27's
diagnosis, Stage 28's watch and Stage 29's owner reports: each built once,
before the first round, so a configuration that cannot run is refused at start
rather than in a round (D-168), and run in that order each round, each reading
what the ones before it wrote. Apart from :mod:`meridian.cli_jobs`, which
builds the scheduling half and runs the loop.

**The jobs modules are imported inside each function**, for the reason
:mod:`meridian.cli_jobs` gives: the command tree is imported by every API
worker, which must never load them.

Reference: docs/DECISIONS.md D-110, D-168, D-182, D-263, D-272, D-294, D-304.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg

from meridian.config import Settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.store.pool import CONNECT_TIMEOUT_S

if TYPE_CHECKING:
    from meridian.jobs.diagnosis_round import DatabaseDiagnosisWork
    from meridian.jobs.reliability_round import DatabaseReliabilityWork
    from meridian.jobs.report_round import DatabaseReportWork
    from meridian.jobs.verdict_round import DatabaseVerdictWork
    from meridian.jobs.watch_round import DatabaseWatchWork

__all__ = ["LaterTasks", "later_tasks", "run_later_tasks"]


def reliability_task(settings: Settings) -> DatabaseReliabilityWork:
    """The sweep and the classification, under the deployment's configuration."""
    # Inside the function: see the module docstring.
    from meridian.jobs.reliability_round import (  # noqa: PLC0415
        DatabaseReliabilityWork,
    )
    from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: PLC0415
    from meridian.reliability.config import (  # noqa: PLC0415
        load_deployed_reliability_config,
    )

    url = settings.psycopg_url
    return DatabaseReliabilityWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        lambda conn, now: PsycopgRegistry(
            conn,
            pepper=settings.token_hash_pepper,
            recovery_window_s=settings.registration_recovery_window_s,
            now_utc=now,
        ),
        load_deployed_reliability_config().classification,
    )


def verdict_task(settings: Settings) -> DatabaseVerdictWork | None:
    """The verdict task under ``VERDICT_MODEL``, or ``None`` when it names none.

    Raises:
        MalformedModelError: The directory is not a verdict model, or does
            not match its manifest.
    """
    # Inside the function: see the module docstring.
    from meridian.jobs.verdict_round import DatabaseVerdictWork  # noqa: PLC0415
    from meridian.prediction.verdict_files import (  # noqa: PLC0415
        load_verdict_model,
    )
    from meridian.verdict_build import registry_for  # noqa: PLC0415

    if not settings.verdict_model:
        return None
    model = load_verdict_model(Path(settings.verdict_model))
    url = settings.psycopg_url
    return DatabaseVerdictWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        lambda conn, now: registry_for(conn, settings, now),
        model,
    )


def diagnosis_task(
    settings: Settings, verdicts: DatabaseVerdictWork | None
) -> DatabaseDiagnosisWork:
    """The diagnosis task, reading partial decodes against the verdict model's method.

    Raises:
        ReliabilityConfigError: The deployment's reliability file is refused.
    """
    # Inside the function: see the module docstring.
    from meridian.jobs.diagnosis_round import DatabaseDiagnosisWork  # noqa: PLC0415
    from meridian.reliability.config import (  # noqa: PLC0415
        load_deployed_reliability_config,
    )
    from meridian.verdict_build import registry_for  # noqa: PLC0415

    url = settings.psycopg_url
    return DatabaseDiagnosisWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        lambda conn, now: registry_for(conn, settings, now),
        SkyfieldOrbitService(),
        config=load_deployed_reliability_config(),
        verdict_method=None if verdicts is None else verdicts.method,
    )


def watch_task(settings: Settings) -> DatabaseWatchWork:
    """The receive-chain watch task.

    Raises:
        ReliabilityConfigError: The deployment's reliability file is refused.
    """
    # Inside the function: see the module docstring.
    from meridian.jobs.watch_round import DatabaseWatchWork  # noqa: PLC0415
    from meridian.reliability.config import (  # noqa: PLC0415
        load_deployed_reliability_config,
    )

    url = settings.psycopg_url
    return DatabaseWatchWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        SkyfieldOrbitService(),
        config=load_deployed_reliability_config(),
    )


def report_task(
    settings: Settings, verdicts: DatabaseVerdictWork | None
) -> DatabaseReportWork:
    """The owner-report task, reading what the deployment classifies and diagnoses.

    Raises:
        NotificationsConfigError: The notifications file is refused.
        ReliabilityConfigError: The deployment's reliability file is refused.
    """
    # Inside the function: see the module docstring.
    from meridian.jobs.report_round import DatabaseReportWork  # noqa: PLC0415
    from meridian.notifications.compose import deployment_of  # noqa: PLC0415
    from meridian.notifications.config import (  # noqa: PLC0415
        load_deployed_notifications_config,
    )
    from meridian.reliability.config import (  # noqa: PLC0415
        load_deployed_reliability_config,
    )

    url = settings.psycopg_url
    return DatabaseReportWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        config=load_deployed_notifications_config(),
        deployment=deployment_of(
            load_deployed_reliability_config(),
            None if verdicts is None else verdicts.method,
        ),
    )


@dataclass(frozen=True, slots=True)
class LaterTasks:
    """The tasks after scheduling, in the order a round runs them."""

    reliability: DatabaseReliabilityWork
    verdicts: DatabaseVerdictWork | None
    diagnosis: DatabaseDiagnosisWork
    watch: DatabaseWatchWork
    reports: DatabaseReportWork


def later_tasks(settings: Settings) -> LaterTasks:
    """Every task after scheduling, as configured, each refused before any round.

    Raises:
        ReliabilityConfigError: The deployment's reliability file is refused.
        NotificationsConfigError: The notifications file is refused.
        MalformedModelError: The verdict model is not one.
    """
    verdicts = verdict_task(settings)
    return LaterTasks(
        reliability=reliability_task(settings),
        verdicts=verdicts,
        diagnosis=diagnosis_task(settings, verdicts),
        watch=watch_task(settings),
        reports=report_task(settings, verdicts),
    )


def run_later_tasks(later: LaterTasks, now: datetime) -> bool:
    """The rest of a round, in order; True if every task in it completed.

    Stage 20's sweep and classification, then the verdicts, the diagnosis, the
    watch and the owner reports, each reading what the ones before it wrote.
    """
    # Inside the function: see the module docstring.
    from meridian.jobs.diagnosis_round import run_diagnosis_round  # noqa: PLC0415
    from meridian.jobs.reliability_round import (  # noqa: PLC0415
        run_reliability_round,
    )
    from meridian.jobs.report_round import run_report_round  # noqa: PLC0415
    from meridian.jobs.verdict_round import run_verdict_round  # noqa: PLC0415
    from meridian.jobs.watch_round import run_watch_round  # noqa: PLC0415

    checked = run_reliability_round(later.reliability, now)
    applied = run_verdict_round(later.verdicts, now)
    diagnosed = run_diagnosis_round(later.diagnosis, now)
    watched = run_watch_round(later.watch, now)
    reported = run_report_round(later.reports, now)
    return None not in (
        checked.expired,
        checked.classified,
        diagnosed,
        watched,
        reported,
    ) and (later.verdicts is None or applied is not None)

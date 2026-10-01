"""``meridian jobs run`` — generation, scheduling and reliability, kept running.

The process the ``jobs`` service runs in every deployment (D-110). Each round
generates passes over the next ``SCHEDULE_HORIZON_S`` and schedules them under
the ``schedule.toml`` named by ``SCHEDULE_CONFIG`` — configuration A on the
elevation proxy when none is (D-168, D-170) — then expires work nobody took and
classifies every pass that has settled (D-182, D-183), then waits
``SCHEDULE_INTERVAL_S``; SIGTERM or SIGINT ends the wait and the process exits
after the task in hand. Its metrics are served on ``JOBS_METRICS_PORT`` behind
the same token as the API's (D-109).

``--once`` runs a single round with no listener and exits — non-zero if any
task failed — which is what an operator uses to fill a horizon by hand and what
the tests use to exercise the real wiring.

**The jobs modules are imported inside the handler, not at the top.** ``meridian
serve`` is reached through the same command tree, and uvicorn starts each API
worker by spawning a fresh interpreter that re-imports it. A top-level import
here put ``meridian.jobs.job_metrics`` in every API worker, whose multiprocess
metrics directory then published ``meridian_passes_computed 0`` once per worker
— a zero from a process that never computes passes, which D-086 refuses.
``tests/unit/test_cli_jobs.py`` pins that importing the command tree loads none
of them.

Reference: docs/DECISIONS.md D-066, D-109, D-110, D-182, D-183.
"""

from __future__ import annotations

import argparse
import logging.config
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg

from meridian.cli_serve import LOG_LEVELS, logging_configuration
from meridian.cli_snapshot import datasets_root
from meridian.config import Settings, load_settings
from meridian.config_checks import DATABASE_PASSWORD, METRICS_TOKEN
from meridian.metrics.exposition import MULTIPROCESS_DIRECTORY_VARIABLE
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.prediction.live import LiveScoringError
from meridian.prediction.score import MalformedModelError
from meridian.scheduler.schedule_config import (
    ScheduleConfig,
    ScheduleConfigError,
    load_schedule_config,
)
from meridian.scheduler.scoring import ScorerSource
from meridian.store.pool import CONNECT_TIMEOUT_S

if TYPE_CHECKING:
    from meridian.jobs.reliability_round import DatabaseReliabilityWork
    from meridian.jobs.rounds import DatabaseRoundWork
    from meridian.jobs.verdict_round import DatabaseVerdictWork

__all__ = ["JOBS_SECRETS", "add_jobs_parser", "run_jobs"]

JOBS_SECRETS = frozenset({DATABASE_PASSWORD, METRICS_TOKEN})
"""The secrets compose gives the jobs process, and so the only ones it answers for."""

_EXIT_FAILED = 1


def add_jobs_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian jobs`` and its one action."""
    jobs = subcommands.add_parser(
        "jobs",
        help="run pass generation and scheduling on a timer",
        description=(
            "Keeps the horizon generated and scheduled without anyone running "
            "a command, so a deployment schedules with no simulator (D-110)."
        ),
    )
    actions = jobs.add_subparsers(dest="action", metavar="<action>")
    run = actions.add_parser("run", help="run rounds until stopped")
    run.add_argument("--once", action="store_true", help="run one round, then exit")
    run.add_argument(
        "--metrics-host",
        default="127.0.0.1",
        help="address the metrics listener binds; the image passes 0.0.0.0",
    )


def _refusal(settings: Settings) -> str | None:
    """Why this configuration must not start, or ``None`` when it may.

    The first failing check wins; they are listed in the order an operator would
    fix them.
    """
    checks = (
        (
            settings.api_log_level.strip().lower() not in LOG_LEVELS,
            f"API_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}",
        ),
        (
            settings.schedule_interval_s < 1,
            "SCHEDULE_INTERVAL_S must be at least 1",
        ),
        (
            settings.schedule_horizon_s <= settings.schedule_interval_s,
            "SCHEDULE_HORIZON_S must exceed SCHEDULE_INTERVAL_S, or passes rising "
            "between one round's horizon and the next round are never scheduled",
        ),
        (
            bool(os.environ.get(MULTIPROCESS_DIRECTORY_VARIABLE)),
            f"{MULTIPROCESS_DIRECTORY_VARIABLE} is set; the jobs process is one "
            "process and its metrics would not reach its own listener (D-109)",
        ),
    )
    return next((reason for failed, reason in checks if failed), None)


def _schedule(settings: Settings) -> tuple[ScheduleConfig, ScorerSource]:
    """The schedule every round runs, with its model loaded once to prove it loads.

    Raises:
        ScheduleConfigError: The file is refused, or names another
            configuration's model.
        LiveScoringError: The model, or the history it reads, cannot score.
    """
    named = settings.schedule_config
    config = load_schedule_config(Path(named) if named else None)
    scorers = ScorerSource(config, datasets_root(None))
    scorers.current()
    return config, scorers


def _rounds(settings: Settings, scorers: ScorerSource) -> DatabaseRoundWork:
    """The real tasks, connecting as every other ``meridian`` command does."""
    # Inside the function: see the module docstring.
    from meridian.jobs.rounds import DatabaseRoundWork  # noqa: PLC0415

    url = settings.psycopg_url
    return DatabaseRoundWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        SkyfieldOrbitService(),
        scorers.current,
        datasets_root(None),
    )


def _reliability(settings: Settings) -> DatabaseReliabilityWork:
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


def _verdicts(settings: Settings) -> DatabaseVerdictWork | None:
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


def run_jobs(args: argparse.Namespace) -> int:
    """Handle ``meridian jobs run``."""
    # Inside the function: see the module docstring.
    from meridian.jobs.metrics_listener import start_metrics_listener  # noqa: PLC0415
    from meridian.jobs.reliability_round import (  # noqa: PLC0415
        run_reliability_round,
    )
    from meridian.jobs.rounds import (  # noqa: PLC0415
        RoundPlan,
        run_round,
        run_until_stopped,
    )
    from meridian.jobs.verdict_round import run_verdict_round  # noqa: PLC0415
    from meridian.reliability.config import ReliabilityConfigError  # noqa: PLC0415

    # The jobs process is given the database and the metrics token and nothing
    # else, so only those are refused as placeholders (D-206).
    settings = load_settings(secrets_held=JOBS_SECRETS)
    refusal = _refusal(settings)
    if refusal is not None:
        print(f"meridian jobs run: {refusal}", file=sys.stderr)  # noqa: T201
        return _EXIT_FAILED

    try:
        config, scorers = _schedule(settings)
        reliability = _reliability(settings)
        verdicts = _verdicts(settings)
    except (
        ScheduleConfigError,
        LiveScoringError,
        ReliabilityConfigError,
        MalformedModelError,
        OSError,
    ) as exc:
        # Refused before any round: a schedule or a classification that cannot
        # be made as configured must not quietly become another one (D-168).
        print(f"meridian jobs run: {exc}", file=sys.stderr)  # noqa: T201
        return _EXIT_FAILED

    logging.config.dictConfig(
        logging_configuration(settings.api_log_level.strip().lower())
    )
    work = _rounds(settings, scorers)
    plan = RoundPlan(
        horizon=timedelta(seconds=settings.schedule_horizon_s), config=config
    )

    def round_once() -> bool:
        """One whole round; True if every task in it completed."""
        now = datetime.now(UTC)
        outcome = run_round(work, plan, now)
        checked = run_reliability_round(reliability, now)
        applied = run_verdict_round(verdicts, now)
        return None not in (
            outcome.generated,
            outcome.scheduled,
            outcome.profiled,
            checked.expired,
            checked.classified,
        ) and (verdicts is None or applied is not None)

    if args.once:
        return 0 if round_once() else _EXIT_FAILED

    start_metrics_listener(
        args.metrics_host, settings.jobs_metrics_port, settings.metrics_token
    )
    stop = threading.Event()
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda _signal, _frame: stop.set())
    run_until_stopped(round_once, settings.schedule_interval_s, stop)
    return 0

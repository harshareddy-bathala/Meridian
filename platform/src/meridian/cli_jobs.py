"""``meridian jobs run`` — pass generation and scheduling, kept running.

The process the ``jobs`` service runs in every deployment (D-110). Each round
generates passes over the next ``SCHEDULE_HORIZON_S`` and schedules them under
configuration A, then waits ``SCHEDULE_INTERVAL_S``; SIGTERM or SIGINT ends the
wait and the process exits after the task in hand. Its metrics are served on
``JOBS_METRICS_PORT`` behind the same token as the API's (D-109).

``--once`` runs a single round with no listener and exits — non-zero if either
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

Reference: docs/DECISIONS.md D-066, D-109, D-110.
"""

from __future__ import annotations

import argparse
import logging.config
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import psycopg

from meridian.cli_schedule import PHASE_1_TURNAROUND_S
from meridian.cli_serve import LOG_LEVELS, logging_configuration
from meridian.config import Settings, load_settings
from meridian.metrics.exposition import MULTIPROCESS_DIRECTORY_VARIABLE
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.store.pool import CONNECT_TIMEOUT_S

if TYPE_CHECKING:
    from meridian.jobs.rounds import DatabaseRoundWork

__all__ = ["JOBS_MODEL_CONFIG", "add_jobs_parser", "run_jobs"]

JOBS_MODEL_CONFIG = "A"
"""The configuration every round schedules under, until Stage 18.

A is the elevation baseline, the one configuration that needs no trained model.
Stage 18 supplies the constrained scheduler this changes to (D-110).
"""

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


def _rounds(settings: Settings) -> DatabaseRoundWork:
    """The real tasks, connecting as every other ``meridian`` command does."""
    # Inside the function: see the module docstring.
    from meridian.jobs.rounds import DatabaseRoundWork  # noqa: PLC0415

    url = settings.psycopg_url
    return DatabaseRoundWork(
        lambda: psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S),
        SkyfieldOrbitService(),
    )


def run_jobs(args: argparse.Namespace) -> int:
    """Handle ``meridian jobs run``."""
    # Inside the function: see the module docstring.
    from meridian.jobs.metrics_listener import start_metrics_listener  # noqa: PLC0415
    from meridian.jobs.rounds import (  # noqa: PLC0415
        RoundPlan,
        run_round,
        run_until_stopped,
    )

    settings = load_settings()
    refusal = _refusal(settings)
    if refusal is not None:
        print(f"meridian jobs run: {refusal}", file=sys.stderr)  # noqa: T201
        return _EXIT_FAILED

    logging.config.dictConfig(
        logging_configuration(settings.api_log_level.strip().lower())
    )
    work = _rounds(settings)
    plan = RoundPlan(
        horizon=timedelta(seconds=settings.schedule_horizon_s),
        model_config=JOBS_MODEL_CONFIG,
        turnaround_s=PHASE_1_TURNAROUND_S,
    )

    if args.once:
        outcome = run_round(work, plan, datetime.now(UTC))
        completed = outcome.generated is not None and outcome.scheduled is not None
        return 0 if completed else _EXIT_FAILED

    start_metrics_listener(
        args.metrics_host, settings.jobs_metrics_port, settings.metrics_token
    )
    stop = threading.Event()
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda _signal, _frame: stop.set())
    run_until_stopped(
        lambda: run_round(work, plan, datetime.now(UTC)),
        settings.schedule_interval_s,
        stop,
    )
    return 0

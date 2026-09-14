"""``meridian serve`` — the API as the image runs it.

A thin, deliberate wrapper around uvicorn. It exists for three things a bare
``uvicorn meridian.api.app:app`` does not do:

- **It applies ``API_LOG_LEVEL``,** which the settings have loaded since Stage 1
  and nothing read, and puts uvicorn's loggers and Meridian's on one line format
  so ``docker compose logs`` reads as one stream (D-114).
- **It runs several workers correctly.** ``API_WORKERS`` above one needs
  ``prometheus_client``'s multiprocess directory, or every scrape reports
  whichever worker answered (D-109). ``serve`` refuses that combination instead
  of starting with wrong numbers, and empties the directory before the workers
  start, because files left by a previous run would be summed into this one.
- **It shuts down within compose's stop window.** uvicorn already finishes
  in-flight requests on SIGTERM; the graceful timeout here is set under compose's
  ten-second default, so a request still open at the deadline is cut rather than
  the whole process being killed mid-write.

No gauge is kept in the API process's own registry — the database-derived figures
are computed per scrape (D-109) — so no worker-exit clean-up of gauge files is
needed; counters and histograms from a finished worker are meant to persist.

Reference: docs/DECISIONS.md D-051, D-109, D-114.
"""

from __future__ import annotations

import argparse
import logging.config
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from meridian.config import load_settings
from meridian.metrics.exposition import MULTIPROCESS_DIRECTORY_VARIABLE

__all__ = [
    "APPLICATION",
    "GRACEFUL_SHUTDOWN_TIMEOUT_S",
    "LOG_LEVELS",
    "ServeOptions",
    "add_serve_parser",
    "logging_configuration",
    "prepare_multiprocess_directory",
    "run_serve",
]

APPLICATION = "meridian.api.app:app"
"""An import string rather than the object: uvicorn needs one to start workers."""

GRACEFUL_SHUTDOWN_TIMEOUT_S = 8
"""How long in-flight requests get after SIGTERM before they are cut.

Under Docker Compose's ten-second stop grace period, so uvicorn ends the process
itself rather than being killed by the engine with a response half written.
"""

LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")
"""The levels uvicorn accepts, in its own spelling."""

_LINE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_EXIT_REFUSED = 1


@dataclass(frozen=True, slots=True)
class ServeOptions:
    """What one ``serve`` invocation runs with, after defaults are applied."""

    host: str
    port: int
    workers: int
    log_level: str


def add_serve_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian serve``."""
    serve = subcommands.add_parser(
        "serve",
        help="run the platform API",
        description=(
            "Runs the API under uvicorn with the configured log level and worker "
            "count. Several workers need PROMETHEUS_MULTIPROC_DIR set (D-109)."
        ),
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to bind; the image passes 0.0.0.0 (default: 127.0.0.1)",
    )
    serve.add_argument("--port", type=int, default=None, help="default: API_PORT")
    serve.add_argument(
        "--workers", type=int, default=None, help="default: API_WORKERS, or 1"
    )


def logging_configuration(level: str) -> dict[str, object]:
    """One line format for uvicorn's loggers and the platform's, at ``level``.

    Args:
        level: One of :data:`LOG_LEVELS`.

    Returns:
        A ``logging.config.dictConfig`` document writing to standard error.
    """
    python_level = "DEBUG" if level == "trace" else level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"line": {"format": _LINE_FORMAT}},
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "line",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"handlers": ["stderr"], "level": python_level},
        "loggers": {
            **{
                name: {"handlers": [], "level": python_level, "propagate": True}
                for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
            },
            # Importing alembic, which the schema-currency metric needs, announces
            # a dozen plugins at info in every worker. They describe alembic's
            # own start-up, not the platform's, so they are held to warnings.
            "alembic": {"handlers": [], "level": "WARNING", "propagate": True},
        },
    }


def prepare_multiprocess_directory(directory: Path) -> None:
    """Create the metrics directory, or empty one a previous run left behind.

    Only the ``.db`` files ``prometheus_client`` writes are removed; the directory
    itself may be a mount point and is kept.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for leftover in directory.glob("*.db"):
        leftover.unlink()


def _options(args: argparse.Namespace) -> ServeOptions:
    settings = load_settings()
    return ServeOptions(
        host=args.host,
        port=args.port if args.port is not None else settings.api_port,
        workers=args.workers if args.workers is not None else settings.api_workers,
        log_level=settings.api_log_level.strip().lower(),
    )


def _refusal(options: ServeOptions, multiprocess_directory: str) -> str | None:
    """Why these options must not start, or ``None`` when they may."""
    if options.log_level not in LOG_LEVELS:
        return f"API_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}"
    if options.workers < 1:
        return "the worker count must be at least 1"
    if options.workers > 1 and not multiprocess_directory:
        return (
            f"{options.workers} workers need {MULTIPROCESS_DIRECTORY_VARIABLE} set, "
            "or each scrape reports only the worker that answered it (D-109)"
        )
    return None


def run_serve(args: argparse.Namespace) -> int:
    """Handle ``meridian serve``: validate, prepare, and hand over to uvicorn."""
    options = _options(args)
    multiprocess_directory = os.environ.get(MULTIPROCESS_DIRECTORY_VARIABLE, "")
    refusal = _refusal(options, multiprocess_directory)
    if refusal is not None:
        print(f"meridian serve: {refusal}", file=sys.stderr)  # noqa: T201
        return _EXIT_REFUSED

    if multiprocess_directory:
        prepare_multiprocess_directory(Path(multiprocess_directory))
    configuration = logging_configuration(options.log_level)
    logging.config.dictConfig(configuration)
    uvicorn.run(
        APPLICATION,
        host=options.host,
        port=options.port,
        workers=options.workers,
        log_config=configuration,
        log_level=options.log_level,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_S,
    )
    return 0

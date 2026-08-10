"""``python -m meridian_sim.station`` — virtual stations speaking real MSP.

A **shell**. The arguments, their defaults and the exit code are real; the
stations are not.

``deploy/docker-compose.yml`` runs this module under the ``sim`` profile with
``restart: unless-stopped``, which sets the bar every version of this file has to
clear: it must exist, and it must exit with a message rather than a traceback.
Anything that raises on the way up becomes a container that restarts forever,
reporting the same stack trace to nobody in particular.

What replaces this, at Stage 10, must satisfy the constraint that makes the
simulator worth having at all:

    meridian-sim → meridian-client → HTTP → platform

Every simulated station goes over HTTP through the same client a real one runs,
using only endpoints a real station could call. That is what makes the simulator
evidence: a platform tested against a simulator that reached into the database
directly would only be testing itself.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from meridian_sim import __version__

__all__ = ["main"]

EXIT_NOT_IMPLEMENTED = 2
"""Matches ``meridian.cli``'s code for the same meaning, so a script driving both
reads one convention: 2 is "not built yet", 1 is "ran and could not complete"."""

EXIT_FAILED = 1
"""A bad argument or an unreadable environment variable."""

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_SEED = 4471
"""The seed in deploy/.env.example. Recording the seed is what makes a run
reproducible, so the default is a value the documentation also names rather than
something the process invents at startup."""


class UnreadableEnvironmentError(ValueError):
    """An environment variable this module needs could not be read as a number."""


def _int_env(name: str, default: int) -> int:
    """Read an integer variable, treating unset and empty alike as the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        # Compose passes SIMULATOR_SEED and SIMULATOR_STATION_COUNT straight from
        # `.env`, so a typo there arrives here. Left as a bare int() it surfaced
        # as a traceback from a crash-looping container under `restart:
        # unless-stopped` — which is the failure this module exists to replace.
        raise UnreadableEnvironmentError(
            f"{name} must be an integer, got {raw!r}"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    """Flags override the environment; the environment overrides the defaults.

    The environment layer is not decoration: ``deploy/docker-compose.yml`` passes
    ``MERIDIAN_BASE_URL``, ``SIMULATOR_SEED`` and ``SIMULATOR_STATION_COUNT`` and
    no flags at all, so a parser that ignored them would run against the wrong
    host while reporting the right one.
    """
    parser = argparse.ArgumentParser(
        prog="python -m meridian_sim.station",
        description="Run deterministic virtual stations against a Meridian platform.",
    )
    parser.add_argument(
        "--version", action="version", version=f"meridian-sim {__version__}"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=_int_env("SIMULATOR_STATION_COUNT", 1),
        help="number of virtual stations",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=_int_env("SIMULATOR_SEED", DEFAULT_SEED),
        help="master seed",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MERIDIAN_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        help="platform base URL",
    )
    parser.add_argument(
        "--run-id", default=None, help="simulator run id; generated if omitted"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``.

    Args:
        argv: Command line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        :data:`EXIT_FAILED` for a bad argument or an unreadable environment
        variable, and :data:`EXIT_NOT_IMPLEMENTED` otherwise — which is every
        valid invocation until Stage 10 puts real stations behind it.
    """
    try:
        parser = _build_parser()
    except UnreadableEnvironmentError as exc:
        print(f"meridian_sim.station: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    args = parser.parse_args(argv)

    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    print(  # noqa: T201 — this is a CLI; stderr is the interface
        "meridian_sim.station: not implemented yet.\n"
        f"  Would run:   {args.count} station(s) against {args.base_url}\n"
        f"  Master seed: {args.seed}\n"
        "  Arrives in:  Stage 10 — the deterministic simulator\n"
        "  Which delivers: a virtual station that registers, holds an assignment,\n"
        "                  reports an observation, and keeps simulation provenance\n"
        "                  at every layer — over real MSP, through meridian-client.\n"
        "  Roadmap: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())

"""``python -m meridian_sim.station`` — virtual stations speaking real MSP.

A **shell**. The arguments, their defaults and the exit code are real; the
stations are not. It exists now because ``deploy/docker-compose.yml`` has run
this module under the ``sim`` profile since the compose file was written, and the
module did not exist — so ``--profile sim`` produced a container that died on
``ModuleNotFoundError`` and, under ``restart: unless-stopped``, kept dying.

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

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_SEED = 4471
"""The seed in deploy/.env.example. Recording the seed is what makes a run
reproducible, so the default is a value the documentation also names rather than
something the process invents at startup."""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


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
        ``1`` for a bad argument, and ``EXIT_NOT_IMPLEMENTED`` otherwise — which
        is every valid invocation until Stage 10 puts real stations behind it.
    """
    args = _build_parser().parse_args(argv)

    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)  # noqa: T201
        return 1

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

"""``python -m meridian_sim.station`` — virtual stations speaking real MSP.

Resolves a run from flags and the environment, reads the invites an operator
issued, and hands the whole thing to :class:`~meridian_sim.supervisor.Supervisor`.
Argument handling, exit codes and rendering only: what a run *is* belongs to
:mod:`~meridian_sim.config`, and what it *does* to the supervisor.

``deploy/docker-compose.yml`` runs this under the ``sim`` profile, which sets the
bar it has to clear: nothing here may raise on the way up. A traceback from a
restarting container is the same stack trace reported to nobody every few
seconds, so every foreseeable failure below prints a sentence and returns an
exit code instead.

The constraint that makes the simulator worth having::

    meridian-sim → meridian-client → HTTP → platform

Every virtual station goes over HTTP through the same client a real one runs,
using only endpoints a real station could call. A platform tested against a
simulator that reached into the database would only be testing itself.

Reference: docs/DECISIONS.md D-075, D-076, D-079, D-080.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from meridian_sim import __version__
from meridian_sim.config import RunConfig
from meridian_sim.faults import SCENARIOS
from meridian_sim.supervisor import Supervisor
from meridian_sim.virtual_station import RegistrationNeededError

__all__ = ["main"]

EXIT_FAILED = 1
"""A bad argument, an unreadable environment variable, or a run that could not
start. Matches ``meridian.cli``'s code for the same meaning."""

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_SEED = 4471
"""The seed in deploy/.env.example. Recording the seed is what makes a run
reproducible, so the default is a value the documentation also names rather than
something the process invents at startup."""

DEFAULT_STATE_DIR = "/var/lib/meridian-sim"
"""Where a fleet keeps its identities between runs.

Outside the image, so a container that is replaced does not take its stations'
credentials with it — every one of them would re-register and burn an invite
(D-080).
"""


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


def _text_env(name: str, default: str) -> str:
    """Read a text variable, treating unset and empty alike as the default."""
    return os.environ.get(name, "").strip() or default


def _build_parser() -> argparse.ArgumentParser:
    """Flags override the environment; the environment overrides the defaults.

    The environment layer is not decoration: ``deploy/docker-compose.yml`` passes
    its settings as variables and no flags at all, so a parser that ignored them
    would run against the wrong host while reporting the right one.
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
        help="master seed; every station is derived from it and its own index",
    )
    parser.add_argument(
        "--base-url",
        default=_text_env("MERIDIAN_BASE_URL", DEFAULT_BASE_URL),
        help="platform base URL",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="simulator run id; derived from the seed if omitted",
    )
    parser.add_argument(
        "--state-dir",
        default=_text_env("SIMULATOR_STATE_DIR", DEFAULT_STATE_DIR),
        help="where stations keep credentials, held work and their outboxes",
    )
    parser.add_argument(
        "--scenario",
        default=_text_env("SIMULATOR_SCENARIO", "clean"),
        choices=sorted(SCENARIOS),
        help="which faults to inject",
    )
    parser.add_argument(
        "--invites",
        default=_text_env("SIMULATOR_INVITES_FILE", ""),
        metavar="PATH",
        help=(
            "file of invite tokens, one per line, as `meridian invite create "
            "--count N` prints them. Only needed the first time a fleet starts"
        ),
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="stop after this many rounds; runs until stopped if omitted",
    )
    return parser


def _read_invites(path: str) -> list[str]:
    """The invite tokens a run was given, or none.

    Raises:
        OSError: The file was named and could not be read. Not swallowed: an
            operator who passed ``--invites`` meant it, and a fleet that
            silently started with none would fail one station at a time with a
            less useful message.
    """
    if not path:
        return []
    return [
        line.strip()
        for line in Path(path).read_text("utf-8").splitlines()
        if line.strip()
    ]


def _config_from(args: argparse.Namespace) -> RunConfig:
    """The run these arguments describe."""
    return RunConfig(
        master_seed=args.seed,
        run_id=args.run_id or f"sim-{args.seed}",
        station_count=args.count,
        base_url=args.base_url,
        state_dir=Path(args.state_dir),
        scenario=args.scenario,
    )


def _run(config: RunConfig, invites: list[str], rounds: int | None) -> int:
    """Bring the fleet up and tick it, reporting anything that stops it."""
    with Supervisor(config, invites) as supervisor:
        try:
            station_ids = supervisor.bring_up()
        except RegistrationNeededError as exc:
            print(f"meridian_sim.station: {exc}", file=sys.stderr)  # noqa: T201
            return EXIT_FAILED
        except httpx.HTTPError as exc:
            print(  # noqa: T201 — this is a CLI; stderr is the interface
                f"meridian_sim.station: cannot reach {config.base_url}: {exc}",
                file=sys.stderr,
            )
            return EXIT_FAILED

        print(  # noqa: T201
            f"{len(station_ids)} station(s) up against {config.base_url}\n"
            f"  run id:   {config.run_id}\n"
            f"  seed:     {config.master_seed}\n"
            f"  scenario: {config.scenario}",
            file=sys.stderr,
        )
        supervisor.run(stop_after_rounds=rounds)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``.

    Args:
        argv: Command line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        ``0`` when the run finished — including one that ended because every
        station stopped, which is the expected result of the ``revoked``
        scenario. :data:`EXIT_FAILED` for a bad argument, an unreadable invite
        file, or a platform that could not be reached.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        parser = _build_parser()
    except UnreadableEnvironmentError as exc:
        print(f"meridian_sim.station: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    args = parser.parse_args(argv)
    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    try:
        invites = _read_invites(args.invites)
    except OSError as exc:
        print(f"meridian_sim.station: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    return _run(_config_from(args), invites, args.rounds)


if __name__ == "__main__":
    sys.exit(main())

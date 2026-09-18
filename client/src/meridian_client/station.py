"""``python -m meridian_client.station`` — run a registered station.

Reads one configuration file, loads the identity registration left in the state
directory, and ticks the loop on the cadence the platform asked for. Argument
handling, exit codes and logging only: what a station *is* belongs to
:mod:`meridian_client.station_config`, how it is assembled to
:mod:`meridian_client.station_wiring`, and what it *does* to
:class:`~meridian_client.station_loop.StationLoop`.

**It does not register.** Registration consumes an invite and mints the one copy
of a bearer token (D-023), so it is an operator's deliberate step, not something
a service does on the way up — a station that registered itself whenever its
state directory looked empty would burn an invite on every wiped disk. A station
with no credentials says so and stops.

Every foreseeable failure prints a sentence and returns an exit code. A station
runs unattended, and a traceback is a stack trace reported to nobody.

Reference: docs/DECISIONS.md D-023, D-024, D-030, D-120 to D-127.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from meridian_client.credentials import StationCredentials, load_credentials
from meridian_client.reception.protocols import StationClocks
from meridian_client.station_config import (
    ConfigError,
    StationConfig,
    load_station_config,
)
from meridian_client.station_wiring import build_loop, open_transport
from meridian_client.transport import UNAUTHORIZED

__all__ = ["main"]

EXIT_FAILED = 1
"""A bad configuration, an unregistered station, or a revoked token."""


def build_parser() -> argparse.ArgumentParser:
    """The station runner's arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m meridian_client.station",
        description="Run a registered station: heartbeat, receive, decode, report.",
    )
    parser.add_argument("--config", type=Path, required=True, help="station TOML file")
    parser.add_argument(
        "--ticks",
        type=int,
        help="stop after this many ticks; default is to run until stopped",
    )
    return parser


class _RefusedError(ValueError):
    """A reason this station will not start, already written for an operator."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the station until its token is revoked or its ticks run out.

    Returns:
        ``0`` when the run ended on its own terms, ``EXIT_FAILED`` with a
        sentence on stderr otherwise.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        reason = _start(args)
    except (ConfigError, ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_FAILED
    if reason == UNAUTHORIZED:
        sys.stderr.write(
            "the platform revoked this station's token; an operator must issue a "
            "replacement invite bound to this station (D-024, D-034)\n"
        )
        return EXIT_FAILED
    return 0


def _start(args: argparse.Namespace) -> str | None:
    """Load what the station is, refuse if it is not registered, and run it.

    Raises:
        ConfigError: The configuration cannot be used.
        ValueError: The credential file is unreadable, or absent — a station
            that registered itself whenever its state directory looked empty
            would burn an invite on every wiped disk (D-023).
        OSError: The state directory cannot be read or written.
    """
    config = load_station_config(args.config)
    credentials = load_credentials(config.paths.credentials)
    if credentials is None:
        raise _RefusedError(
            f"no station is registered in {config.paths.state_dir}: register one "
            "with an invite before running it"
        )
    return _run(config, credentials, args.ticks)


def _run(
    config: StationConfig, credentials: StationCredentials, ticks: int | None
) -> str | None:
    """Build the loop and run it, closing the transport on the way out."""
    clocks = StationClocks()
    with open_transport(config, credentials) as transport:
        loop = build_loop(config, credentials, clocks, transport)
        return loop.run(stop_after_ticks=ticks)


if __name__ == "__main__":
    sys.exit(main())

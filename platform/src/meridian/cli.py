"""The ``meridian`` command.

``platform/pyproject.toml`` has declared this entry point since the repository
was scaffolded, and until now the module behind it did not exist — so installing
the distribution produced a console script that died with an ``ImportError``
naming a file nobody had written. A declared executable that does not exist is
worse than an absent one: it is discovered by whoever is trying to use it, at the
moment they need it.

Every subcommand here is a **shell**. The parser, the arguments and the exit codes
are real; the work is not. Each one names the stage that implements it, so
``meridian invite create`` answers a question instead of raising.

``--version`` is real, because the container smoke test uses it to prove the
distribution installed and imports cleanly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from meridian import __version__

__all__ = ["main"]

EXIT_NOT_IMPLEMENTED = 2
"""Distinct from 1. A caller can tell "this command does not work yet" from
"this command ran and failed", which matters once these are wired into scripts."""


def _pending(command: str, stage: str, gate: str) -> int:
    """Report that ``command`` is not built yet, and say what will build it."""
    print(  # noqa: T201 — this is a CLI; stdout is the interface
        f"meridian {command}: not implemented yet.\n"
        f"  Arrives in: {stage}\n"
        f"  Which delivers: {gate}\n"
        f"  Roadmap: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Control platform for satellite ground stations.",
    )
    parser.add_argument("--version", action="version", version=f"meridian {__version__}")
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    invite = subcommands.add_parser(
        "invite",
        help="issue, list and revoke registration invites",
        description=(
            "Invites are rows, not a configuration value (D-020). A single "
            "environment variable cannot be consumed, cannot be revoked per "
            "operator, and cannot admit a second station."
        ),
    )
    invite_actions = invite.add_subparsers(dest="action", metavar="<action>")
    create = invite_actions.add_parser("create", help="issue one invite and print it once")
    create.add_argument("--label", required=True, help="who this invite is for")
    create.add_argument("--expires-in-days", type=int, default=None)
    create.add_argument(
        "--for-station",
        default=None,
        metavar="STATION_ID",
        help=(
            "bind the invite to an existing station, rotating its token instead "
            "of admitting a new one — the recovery path for a station that "
            "received 401 (D-034)"
        ),
    )
    invite_actions.add_parser("list", help="show invites and their consumption state")
    revoke = invite_actions.add_parser("revoke", help="withdraw an unconsumed invite")
    revoke.add_argument("--label", required=True)

    passes = subcommands.add_parser("passes", help="generate pass windows")
    passes_actions = passes.add_subparsers(dest="action", metavar="<action>")
    generate = passes_actions.add_parser("generate", help="compute passes over a horizon")
    generate.add_argument("--from", dest="start", required=True, help="ISO-8601 UTC")
    generate.add_argument("--to", dest="end", required=True, help="ISO-8601 UTC")

    subcommands.add_parser("serve", help="run the API (use uvicorn directly for now)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    action = getattr(args, "action", None)
    if action is None and args.command in {"invite", "passes"}:
        parser.parse_args([args.command, "--help"])  # exits

    match (args.command, action):
        case ("invite", _):
            return _pending(
                f"invite {action}",
                "Stage 4.2 — invite CLI",
                "one-time invites generated with `secrets`, stored hashed, "
                "displayed in plaintext exactly once",
            )
        case ("passes", _):
            return _pending(
                f"passes {action}",
                "Stage 7 — pass generation",
                "reproducible pass windows from a local element set, with no "
                "external service on the path",
            )
        case ("serve", _):
            return _pending(
                "serve",
                "Stage 3 — shared MSP infrastructure",
                "the API with versioning, errors and metrics in place; until "
                "then run `uvicorn meridian.api.app:app`",
            )
        case _:  # pragma: no cover — argparse rejects unknown commands first
            parser.print_help(sys.stderr)
            return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

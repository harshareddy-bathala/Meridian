"""The ``meridian`` command.

``platform/pyproject.toml`` has declared this entry point since the repository
was scaffolded, and until now the module behind it did not exist — so installing
the distribution produced a console script that died with an ``ImportError``
naming a file nobody had written. A declared executable that does not exist is
worse than an absent one: it is discovered by whoever is trying to use it, at the
moment they need it.

``invite`` is real (Stage 4.2) — it opens its own short-lived connection rather
than the API's pooled one, because a one-shot process has nothing to pool.
Every other subcommand here is still a **shell**: the parser, the arguments and
the exit codes are real; the work is not. Each one names the stage that
implements it, so a caller gets an answer instead of a traceback.

``--version`` is real, because the container smoke test uses it to prove the
distribution installed and imports cleanly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from meridian import __version__
from meridian.config import load_settings
from meridian.store import invites
from meridian.store.pool import CONNECT_TIMEOUT_S

__all__ = ["main"]

EXIT_NOT_IMPLEMENTED = 2
"""Distinct from 1. A caller can tell "this command does not work yet" from
"this command ran and failed", which matters once these are wired into scripts."""

EXIT_FAILED = 1
"""A command that is implemented and ran, but could not complete — an
unreachable database, an unknown ``--for-station``, a ``revoke`` matching
nothing. Distinct from :data:`EXIT_NOT_IMPLEMENTED` so a script can tell "try
again" from "this command doesn't exist yet"."""

NEEDS_ACTION = frozenset({"passes"})
"""Subcommands that are a noun and mean nothing without a verb after them.

``meridian serve`` is complete on its own; ``meridian passes`` is not, and
printing that subparser's help is more useful than reporting the whole group
as pending. ``invite`` needs the same treatment but is handled separately in
:func:`main`, since it dispatches to real handlers rather than :func:`_pending`.
"""


@dataclass(frozen=True, slots=True)
class _Pending:
    """Which stage builds a subcommand, and what that stage delivers."""

    stage: str
    gate: str


PENDING: dict[str, _Pending] = {
    "passes": _Pending(
        stage="Stage 7 — pass generation",
        gate=(
            "reproducible pass windows from a local element set, with no "
            "external service on the path"
        ),
    ),
    "serve": _Pending(
        stage="Stage 12 — deployment and monitoring",
        gate=(
            "a supervised server with logging and signal handling; the API "
            "itself already runs — use `uvicorn meridian.api.app:app`"
        ),
    ),
}
"""Every subcommand, and the stage that replaces its shell with real work.

A table rather than a ``match`` with one arm per command. The arms were identical
apart from two strings, so the branching carried no information — and each arm was
a separate ``return``, which is how a dispatch function with nothing to decide ends
up over the four-return limit in CLAUDE.local.md section 2.
"""


def _pending(command: str, pending: _Pending) -> int:
    """Report that ``command`` is not built yet, and say what will build it."""
    print(  # noqa: T201 — this is a CLI; stdout is the interface
        f"meridian {command}: not implemented yet.\n"
        f"  Arrives in: {pending.stage}\n"
        f"  Which delivers: {pending.gate}\n"
        f"  Roadmap: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def _run_invite(args: argparse.Namespace) -> int:
    """Dispatch ``meridian invite <action>`` to its handler.

    Opens one connection for the whole invocation and closes it on the way
    out, rather than reaching for :mod:`meridian.store.pool` — a CLI
    invocation is a single short-lived process, so there is nothing here for
    a pool to amortize.
    """
    settings = load_settings()
    try:
        conn = psycopg.connect(settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian invite: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    with conn:
        if args.action == "create":
            return _invite_create(conn, args)
        if args.action == "revoke":
            return _invite_revoke(conn, args)
        return _invite_list(conn)


def _invite_create(conn: invites.Connection, args: argparse.Namespace) -> int:
    """Handle ``meridian invite create``."""
    expires_at = invites.expiry_from_days(args.expires_in_days, now=datetime.now(UTC))
    try:
        token = invites.create_invite(
            conn,
            label=args.label,
            expires_at=expires_at,
            issued_for_station_id=args.for_station,
        )
    except psycopg.errors.ForeignKeyViolation:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian invite create: no such station: {args.for_station}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"Invite for {args.label!r}: {token}")  # noqa: T201
    print(  # noqa: T201
        "This is shown once. It will not be displayed again.", file=sys.stderr
    )
    return 0


def _invite_list(conn: invites.Connection) -> int:
    """Handle ``meridian invite list``."""
    for invite in invites.list_invites(conn):
        state = _invite_state(invite)
        print(f"{invite.label}\t{state}\t{invite.created_at.isoformat()}")  # noqa: T201
    return 0


def _invite_state(invite: invites.Invite) -> str:
    """One word (or two) describing what can still be done with an invite."""
    if invite.consumed_at is not None:
        return f"consumed by {invite.consumed_by_station_id}"
    # The database decides this, not a comparison here — an invite revoked
    # moments ago would otherwise still print "pending" (D-046).
    if invite.is_expired:
        return "expired"
    return "pending"


def _invite_revoke(conn: invites.Connection, args: argparse.Namespace) -> int:
    """Handle ``meridian invite revoke``."""
    revoked = invites.revoke_invite(conn, label=args.label)
    if revoked == 0:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian invite revoke: no revocable invite labelled {args.label!r}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"Revoked {revoked} invite(s) labelled {args.label!r}.")  # noqa: T201
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Control platform for satellite ground stations.",
    )
    parser.add_argument(
        "--version", action="version", version=f"meridian {__version__}"
    )
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
    create = invite_actions.add_parser(
        "create", help="issue one invite and print it once"
    )
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
    generate = passes_actions.add_parser(
        "generate", help="compute passes over a horizon"
    )
    generate.add_argument("--from", dest="start", required=True, help="ISO-8601 UTC")
    generate.add_argument("--to", dest="end", required=True, help="ISO-8601 UTC")

    subcommands.add_parser("serve", help="run the API (use uvicorn directly for now)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "invite":
        if args.action is None:
            parser.parse_args(["invite", "--help"])  # exits
        return _run_invite(args)

    pending = PENDING.get(args.command)
    if pending is None:  # no subcommand, or one argparse already rejected
        parser.print_help(sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    action = getattr(args, "action", None)
    if action is None and args.command in NEEDS_ACTION:
        parser.parse_args([args.command, "--help"])  # exits

    command = f"{args.command} {action}" if action else args.command
    return _pending(command, pending)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

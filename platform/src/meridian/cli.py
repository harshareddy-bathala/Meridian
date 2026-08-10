"""The ``meridian`` command — the operator's side of the platform.

``invite`` and ``station`` are the whole surface for admitting a station to the
network and shutting one out again. ``passes`` fills the horizon those stations
will be scheduled against. Each opens its own short-lived connection rather than
the API's pooled one, because a one-shot process has nothing for a pool to
amortize.

``serve`` is a **shell**: the parser, the arguments and the exit codes are real;
the work is not. It reports which stage of
docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md implements it and exits
:data:`EXIT_NOT_IMPLEMENTED`, so a caller gets an answer rather than a
traceback — see :data:`PENDING`.

This module owns the command tree and the dispatch. ``passes generate`` is the
one subcommand whose implementation needs more than a handler and a print, and
it lives in ``meridian.cli_passes``.

``--version`` prints ``meridian.__version__``, which is what the container
smoke test uses to prove the distribution installed and imports cleanly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from meridian import __version__
from meridian.cli_passes import run_passes
from meridian.config import load_settings
from meridian.store import invites, station_tokens, stations
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


@dataclass(frozen=True, slots=True)
class _Pending:
    """Which stage builds a subcommand, and what that stage delivers."""

    stage: str
    gate: str


PENDING: dict[str, _Pending] = {
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


def _run_station(args: argparse.Namespace) -> int:
    """Run ``meridian station revoke``, the subcommand's only action.

    No dispatch on ``args.action``, unlike :func:`_run_invite`: argparse admits
    exactly one action here and :func:`main` has already sent the actionless
    case to the help text, so a lookup would have one entry and no decision to
    make. A second action turns this into the same shape as ``invite``.

    Its own connection for the invocation, for the same reason
    :func:`_run_invite` opens one: a CLI call is a single short-lived process
    and there is nothing here for a pool to amortize.
    """
    settings = load_settings()
    try:
        conn = psycopg.connect(settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian station: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    with conn:
        return _station_revoke(conn, args)


def _station_revoke(conn: stations.Connection, args: argparse.Namespace) -> int:
    """Handle ``meridian station revoke``."""
    if not station_tokens.revoke_station_token(conn, station_id=args.station_id):
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian station revoke: no station with a live token: {args.station_id}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(  # noqa: T201
        f"Revoked the bearer token for {args.station_id}. Its next request gets 401."
    )
    print(  # noqa: T201
        "The station will stop and surface the failure to its operator rather "
        "than re-register (D-024). To let it back in, issue a replacement "
        "invite bound to it: meridian invite create --label <who> "
        f"--for-station {args.station_id}",
        file=sys.stderr,
    )
    return 0


def _add_invite_parser(
    # argparse gives this object no public name. Spelling out the private one is
    # still better than `Any`, which mypy's disallow_any_explicit forbids and
    # which would switch off checking for every helper below. `from __future__
    # import annotations` means it is never evaluated at runtime.
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian invite`` and its three actions."""
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


def _add_station_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian station`` and its one action."""
    station = subcommands.add_parser(
        "station",
        help="operate on a registered station",
        description=(
            "Revocation is the operator's only way to shut a station out. A "
            "compromised or decommissioned station keeps working until its "
            "token is withdrawn, because a bearer token has no expiry (D-017)."
        ),
    )
    station_actions = station.add_subparsers(dest="action", metavar="<action>")
    revoke = station_actions.add_parser(
        "revoke", help="withdraw a station's bearer token, effective immediately"
    )
    revoke.add_argument(
        "--station-id",
        required=True,
        metavar="STATION_ID",
        help="the station to shut out, as it appears in the register response",
    )


def _add_passes_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian passes`` and its one action."""
    passes = subcommands.add_parser(
        "passes",
        help="generate pass windows",
        description=(
            "Computes every pass every registered station could take over a "
            "horizon, from element sets already in the archive. Nothing here "
            "reaches a network, and running it twice over one horizon stores "
            "nothing the second time (D-063)."
        ),
    )
    passes_actions = passes.add_subparsers(dest="action", metavar="<action>")
    generate = passes_actions.add_parser(
        "generate", help="compute passes over a horizon"
    )
    generate.add_argument("--from", dest="start", required=True, help="ISO-8601 UTC")
    generate.add_argument("--to", dest="end", required=True, help="ISO-8601 UTC")


def _add_pending_parsers(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire the subcommands whose parsers are real and whose work is not."""
    subcommands.add_parser("serve", help="run the API (use uvicorn directly for now)")


def _build_parser() -> argparse.ArgumentParser:
    """The whole command tree.

    One helper per subcommand rather than one long body: the function was
    already at 49 lines against CLAUDE.local.md §2's limit of 40, and a parser
    builder grows by a block every time a command lands.
    """
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Predictive scheduling and reliability for ground stations.",
    )
    parser.add_argument(
        "--version", action="version", version=f"meridian {__version__}"
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    _add_invite_parser(subcommands)
    _add_station_parser(subcommands)
    _add_passes_parser(subcommands)
    _add_pending_parsers(subcommands)

    return parser


IMPLEMENTED: dict[str, Callable[[argparse.Namespace], int]] = {
    "invite": _run_invite,
    "passes": run_passes,
    "station": _run_station,
}
"""Every subcommand that does real work, and the handler that does it.

A table for the same reason :data:`PENDING` is one: the three arms were
identical apart from a name, so branching on the command carried no information
and cost a ``return`` against CLAUDE.local.md §2's limit of four.

Every entry here is a noun that needs a verb — ``meridian passes`` on its own
means nothing — so :func:`main` sends the actionless case to that subparser's
own help rather than guessing.
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    handler = IMPLEMENTED.get(args.command)
    if handler is not None:
        if args.action is None:
            parser.parse_args([args.command, "--help"])  # exits
        return handler(args)

    pending = PENDING.get(args.command)
    if pending is None:  # no subcommand, or one argparse already rejected
        parser.print_help(sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    return _pending(args.command, pending)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

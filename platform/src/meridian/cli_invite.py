"""``meridian invite`` — issuing, listing and withdrawing registration invites.

An invite is the only way a station joins the network, and it is a row rather
than a configuration value (D-020): a single environment variable cannot be
consumed, cannot be revoked per operator, and cannot admit a second station.

Split out of ``meridian.cli`` alongside ``cli_passes`` and ``cli_schedule``, so
that module holds the command tree and each command's work lives beside it.

Reference: docs/DECISIONS.md D-020, D-034, D-046.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import psycopg

from meridian.config import load_settings
from meridian.store import invites
from meridian.store.pool import CONNECT_TIMEOUT_S

__all__ = ["run_invite"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``. Importing it from there would be a
cycle: ``cli`` imports this module to dispatch to it."""


def run_invite(args: argparse.Namespace) -> int:
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
    """Handle ``meridian invite create``, for one invite or for many.

    **Tokens go to stdout, one per line, and everything else goes to stderr.**
    That split is what makes ``--count 50 > invites.txt`` produce a file of
    fifty tokens and nothing else, which is how a fleet of stations is
    provisioned without anyone parsing a sentence. One invite prints one line
    under the same rule, so there is one output shape rather than two.
    """
    expires_at = invites.expiry_from_days(args.expires_in_days, now=datetime.now(UTC))
    for label in _labels_for(args.label, args.count):
        try:
            token = invites.create_invite(
                conn,
                label=label,
                expires_at=expires_at,
                issued_for_station_id=args.for_station,
            )
        except psycopg.errors.ForeignKeyViolation:
            print(  # noqa: T201 — this is a CLI; stderr is the interface
                f"meridian invite create: no such station: {args.for_station}",
                file=sys.stderr,
            )
            return EXIT_FAILED
        print(f"issued {label!r}", file=sys.stderr)  # noqa: T201
        print(token)  # noqa: T201

    print(  # noqa: T201
        "Shown once. They will not be displayed again.", file=sys.stderr
    )
    return 0


def _labels_for(label: str, count: int) -> list[str]:
    """The label each of ``count`` invites is issued under.

    One invite keeps the label exactly as given; several are numbered from one,
    because ``invites.label`` is how an operator later finds or revokes a
    specific invite and fifty rows sharing a name cannot be told apart.
    """
    if count == 1:
        return [label]
    return [f"{label}-{index}" for index in range(1, count + 1)]


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

"""The ``meridian`` command — the operator's side of the platform.

``invite`` and ``station`` are the whole surface for admitting a station to the
network and shutting one out again. ``passes`` fills the horizon those stations
will be scheduled against. Each opens its own short-lived connection rather than
the API's pooled one, because a one-shot process has nothing for a pool to
amortize.

``serve`` runs the API as the image runs it — log level, worker count and the
metrics directory several workers need (``cli_serve``); ``jobs`` and ``db`` are
the scheduled work and the migration check beside it; ``snapshot`` exports,
labels and verifies Stage 15's datasets (``cli_snapshot``); ``model`` fits,
evaluates and shows Stage 17's models (``cli_model``); ``profiles`` writes the
horizon and interference profiles (``cli_profiles``); ``report`` builds
and verifies Stage 22's evaluation reports (``cli_report``). Every command the
operations runbook documents is now built, so the table of commands whose
stage had not arrived went with the last of them.

This module owns the command tree and the dispatch. Each command's work lives
beside it — ``cli_invite``, ``cli_passes`` and ``cli_schedule`` — so the whole
command surface is still readable in one file while no single file grows past
the length a reviewer will actually read.

``--version`` prints ``meridian.__version__``, which is what the container
smoke test uses to prove the distribution installed and imports cleanly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from meridian import __version__
from meridian.cli_catalogue import run_catalogue
from meridian.cli_db import add_db_parser, run_db
from meridian.cli_invite import run_invite
from meridian.cli_jobs import add_jobs_parser, run_jobs
from meridian.cli_model import add_model_parser, run_model
from meridian.cli_passes import run_passes
from meridian.cli_profiles import add_profiles_parser, run_profiles
from meridian.cli_regions import add_regions_parser, run_regions
from meridian.cli_reliability import add_reliability_parser, run_reliability
from meridian.cli_report import add_report_parser, run_report
from meridian.cli_schedule import add_schedule_parser, run_scheduler
from meridian.cli_serve import add_serve_parser, run_serve
from meridian.cli_snapshot import (
    add_snapshot_parser,
    run_snapshot,
)
from meridian.cli_views import add_timing_arguments
from meridian.config import load_settings
from meridian.store import station_tokens, stations
from meridian.store.pool import DatabaseUnreachableError, connect_once

__all__ = ["main"]

EXIT_USAGE = 2
"""No command was given, the code argparse itself uses for a usage error.
Distinct from 1, so a script can tell "called wrongly" from "ran and failed"."""

EXIT_FAILED = 1
"""A command that is implemented and ran, but could not complete — an
unreachable database, an unknown ``--for-station``, a ``revoke`` matching
nothing. Distinct from :data:`EXIT_USAGE` so a script can tell "try again"
from "called wrongly"."""


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
        conn = connect_once(settings)
    except DatabaseUnreachableError as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian station: {exc}", file=sys.stderr
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
        "--count",
        type=int,
        default=1,
        help=(
            "issue this many invites, numbered from the label. Tokens go to "
            "stdout one per line, so a fleet is provisioned by redirecting them "
            "to a file"
        ),
    )
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


def _add_catalogue_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian catalogue`` and its one action."""
    catalogue = subcommands.add_parser(
        "catalogue",
        help="load the satellites this deployment tracks",
        description=(
            "Reads satellites, downlinks and element sets from one local file "
            "and writes whatever the archive does not already hold. A local "
            "file rather than a fetch, so a deployment can schedule and receive "
            "with every external service down (D-079)."
        ),
    )
    catalogue_actions = catalogue.add_subparsers(dest="action", metavar="<action>")
    load = catalogue_actions.add_parser(
        "load", help="add a catalogue document's contents to the archive"
    )
    load.add_argument(
        "--file",
        required=True,
        metavar="PATH",
        help="the catalogue document; see deploy/catalogue/README.md",
    )


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
    """Wire ``meridian passes`` and its two actions."""
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
    add_timing_arguments(
        passes_actions.add_parser(
            "timing",
            help="first detection against predicted rise, clock-corrected",
            description=(
                "Reads the timing_error view (D-177, EVALUATION.md §6.1). An"
                " operator's read; the reported figure comes from a snapshot."
            ),
        )
    )


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
    _add_catalogue_parser(subcommands)
    _add_station_parser(subcommands)
    _add_passes_parser(subcommands)
    add_schedule_parser(subcommands)
    add_serve_parser(subcommands)
    add_jobs_parser(subcommands)
    add_db_parser(subcommands)
    add_snapshot_parser(subcommands)
    add_model_parser(subcommands)
    add_reliability_parser(subcommands)
    add_regions_parser(subcommands)
    add_profiles_parser(subcommands)
    add_report_parser(subcommands)

    return parser


NEEDS_ACTION = frozenset(
    {
        "catalogue",
        "db",
        "invite",
        "jobs",
        "model",
        "passes",
        "regions",
        "reliability",
        "profiles",
        "report",
        "snapshot",
        "station",
    }
)
"""Commands that are a noun and mean nothing without a verb after them.

``meridian schedule`` is a verb already and carries its arguments directly, so
it is absent: sending it to its own help text would make the command
unrunnable. Its one verb, ``evaluate``, is optional.
"""


IMPLEMENTED: dict[str, Callable[[argparse.Namespace], int]] = {
    "catalogue": run_catalogue,
    "db": run_db,
    "invite": run_invite,
    "jobs": run_jobs,
    "model": run_model,
    "passes": run_passes,
    "regions": run_regions,
    "reliability": run_reliability,
    "profiles": run_profiles,
    "report": run_report,
    "schedule": run_scheduler,
    "serve": run_serve,
    "snapshot": run_snapshot,
    "station": _run_station,
}
"""Every subcommand that does real work, and the handler that does it.

A table rather than a ``match`` with one arm per command: the arms were
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
        if args.command in NEEDS_ACTION and args.action is None:
            parser.parse_args([args.command, "--help"])  # exits
        return handler(args)

    parser.print_help(sys.stderr)  # no subcommand; argparse rejected the rest
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

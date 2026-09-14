"""``meridian db status`` — whether the database is at the migration the code expects.

Compose refuses to start the API until ``migrate`` has succeeded, so the two only
disagree after something outside that path: a restore from an older backup, a
container started by hand, or an image rolled back past a migration. Each of those
reads as a missing column in the middle of a heartbeat unless someone asks first,
and this is the asking. The scrape reports the same comparison as
``meridian_schema_up_to_date`` (D-111); this is the form an operator types.

Three verdicts, not two. A database *behind* the code is fixed by running
``migrate``. A database at a revision the code has never heard of was migrated by a
newer image, and running ``migrate`` from this one cannot help — the remedy is the
newer image, so saying "behind" would send the operator the wrong way.

Reference: docs/DECISIONS.md D-019, D-109, D-111.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import psycopg

from meridian.config import load_settings
from meridian.store.pool import CONNECT_TIMEOUT_S
from meridian.store.schema_revision import (
    find_current_revision,
    find_head_revision,
    find_known_revisions,
)

__all__ = ["SchemaVerdict", "add_db_parser", "judge_schema", "run_db"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``; a database that is not at head is a
failure a script should stop on, the same as one it cannot reach."""

UPGRADE_HINT = "docker compose -f deploy/docker-compose.yml run --rm migrate"


@dataclass(frozen=True, slots=True)
class SchemaVerdict:
    """What the comparison found, and the exit status it earns."""

    up_to_date: bool
    summary: str


def judge_schema(
    current: str | None, head: str, known: frozenset[str]
) -> SchemaVerdict:
    """Compare the database's revision with the code's.

    Args:
        current: ``alembic_version``'s revision, or ``None`` before any migration.
        head: The newest revision this code's scripts define.
        known: Every revision this code's scripts define.

    Returns:
        The verdict. Only ``current == head`` is up to date.
    """
    if current == head:
        return SchemaVerdict(up_to_date=True, summary="up to date")
    if current is None:
        return SchemaVerdict(
            up_to_date=False,
            summary=f"no migrations applied. Run: {UPGRADE_HINT}",
        )
    if current in known:
        return SchemaVerdict(
            up_to_date=False, summary=f"behind the code. Run: {UPGRADE_HINT}"
        )
    return SchemaVerdict(
        up_to_date=False,
        summary=(
            "at a revision this code does not define — a newer image migrated it. "
            "Run that image; migrating from this one cannot help"
        ),
    )


def add_db_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian db`` and its one action."""
    db = subcommands.add_parser(
        "db",
        help="inspect the database this deployment runs against",
        description=(
            "Backup and restore are host tools in deploy/tools, because the "
            "platform image carries no PostgreSQL client (D-115)."
        ),
    )
    db_actions = db.add_subparsers(dest="action", metavar="<action>")
    db_actions.add_parser(
        "status", help="compare the database's migration with the code's"
    )


def run_db(_args: argparse.Namespace) -> int:
    """Run ``meridian db status``, the subcommand's only action."""
    head = find_head_revision()
    if head is None:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            "meridian db status: deploy/alembic.ini not found. Run from the "
            "repository root, or inside the image, where it is beneath /app.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    settings = load_settings()
    try:
        with psycopg.connect(
            settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            current = find_current_revision(conn)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201
            f"meridian db status: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    verdict = judge_schema(current, head, find_known_revisions())
    print(f"database revision: {current or 'none'}")  # noqa: T201
    print(f"code expects:      {head}")  # noqa: T201
    print(f"schema:            {verdict.summary}")  # noqa: T201
    return 0 if verdict.up_to_date else EXIT_FAILED

"""Prove a backup restores, without touching the deployment. Stdlib only.

    python deploy/tools/restore_drill.py backups/meridian-2026-09-28T0247Z.dump
    python deploy/tools/restore_drill.py --latest backups

A backup nobody has restored is a hope. `restore.py` replaces the deployment's
database, which is the last thing to try on a Tuesday to find out whether last
night's dump is good. This restores a dump into a scratch database beside the
real one, on the same server and TimescaleDB, and checks it (D-209):

1. the manifest's sha256 and TimescaleDB version, exactly as `restore.py` checks
   them, so a dump the drill passes is one `restore.py` would accept;
2. `pg_restore --exit-on-error --no-acl` into `meridian_restore_drill`, between
   TimescaleDB's pre- and post-restore steps, as `restore.py` does it;
3. the restored database is at the manifest's migration, and every table in it
   can be read, with its row count printed.

The scratch database is dropped afterwards, whether the drill passed or not, and
the live database is never named in any command it runs. It needs free disk for
one more copy of the database while it runs.

`deploy/systemd/meridian-restore-drill.timer` runs it weekly on the newest dump.
`tests/integration/test_restore_drill.py` runs it against the test database.
Exit status: 0 the dump restored and checked out, 1 it did not.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from compose_db import (
    AVAILABLE_TIMESCALEDB_SQL,
    Compose,
    Manifest,
    ToolError,
    add_compose_arguments,
    compose_from,
    manifest_path,
    read_manifest,
    run_sql,
    sha256_of,
)
from restore import POST_RESTORE_SQL, PRE_RESTORE_SQL, judge_restore

DRILL_DATABASE = "meridian_restore_drill"
"""The scratch database. A fixed name, so a drill that died is cleaned by the next."""

RECREATE_SQL = (
    f"drop database if exists {DRILL_DATABASE} with (force);\n"
    f"create database {DRILL_DATABASE};\n"
)
DROP_SQL = f"drop database if exists {DRILL_DATABASE} with (force);\n"
RESTORE_SCRIPT = (
    f'pg_restore -U "$POSTGRES_USER" -d {DRILL_DATABASE} --exit-on-error --no-acl'
)
REVISION_SQL = "select version_num from alembic_version;"
COUNTS_SQL = """
select table_name,
       (xpath('/row/c/text()', query_to_xml(
           format('select count(*) as c from public.%I', table_name),
           false, true, '')))[1]::text
from information_schema.tables
where table_schema = 'public' and table_type = 'BASE TABLE'
order by table_name;
"""
"""One line per table, `name|rows`, counted exactly, in one query."""

DUMP_NAME = re.compile(r"^meridian-.*\.dump$")


@dataclass(frozen=True, slots=True)
class DrillReport:
    """What the restored copy held."""

    revision: str
    row_counts: dict[str, int]


def parse_counts(output: str) -> dict[str, int]:
    """`COUNTS_SQL`'s lines as a mapping from table to rows."""
    counts = {}
    for line in output.splitlines():
        name, _, rows = line.partition("|")
        if name:
            counts[name] = int(rows)
    return counts


def judge_drill(manifest: Manifest, report: DrillReport) -> str | None:
    """Why the restored copy is not the backup it claims to be, or ``None``."""
    if report.revision != manifest.alembic_revision:
        return (
            f"restored at migration {report.revision!r}, the manifest says "
            f"{manifest.alembic_revision!r}"
        )
    if "alembic_version" not in report.row_counts or len(report.row_counts) < 2:
        return f"the restored copy holds only {sorted(report.row_counts)}"
    return None


def latest_dump(directory: Path) -> Path:
    """The newest `meridian-*.dump` in `directory`, by name, which sorts by time."""
    dumps = sorted(p for p in directory.iterdir() if DUMP_NAME.match(p.name))
    if not dumps:
        raise ToolError(f"no meridian-*.dump in {directory}")
    return dumps[-1]


def _step(description: str, command: list[str], stdin: Path | None = None) -> None:
    print(f"-> {description}", flush=True)
    if stdin is None:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    else:
        with stdin.open("rb") as handle:
            result = subprocess.run(
                command, stdin=handle, check=False, capture_output=True
            )
    if result.returncode != 0:
        detail = (
            result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        )
        raise ToolError(
            f"{description} failed (exit {result.returncode}): {detail.strip()}"
        )


def drill(compose: Compose, dump: Path) -> DrillReport:
    """Restore `dump` into the scratch database, check it, and drop it.

    Raises:
        ToolError: The dump was refused, did not restore, or restored to
            something other than its manifest describes.
    """
    manifest = read_manifest(manifest_path(dump))
    available = run_sql(compose, AVAILABLE_TIMESCALEDB_SQL, maintenance=True)
    refusal = judge_restore(manifest, sha256_of(dump), available)
    if refusal is not None:
        raise ToolError(refusal)
    try:
        run_sql(compose, RECREATE_SQL, maintenance=True)
        run_sql(compose, PRE_RESTORE_SQL, database=DRILL_DATABASE)
        _step(
            "pg_restore into the scratch database", compose.in_db(RESTORE_SCRIPT), dump
        )
        run_sql(compose, POST_RESTORE_SQL, database=DRILL_DATABASE)
        report = DrillReport(
            revision=run_sql(compose, REVISION_SQL, database=DRILL_DATABASE),
            row_counts=parse_counts(
                run_sql(compose, COUNTS_SQL, database=DRILL_DATABASE)
            ),
        )
    finally:
        run_sql(compose, DROP_SQL, maintenance=True)
    failure = judge_drill(manifest, report)
    if failure is not None:
        raise ToolError(failure)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("dump", nargs="?", type=Path, help="the dump to drill")
    target.add_argument("--latest", type=Path, metavar="DIR", help="drill its newest")
    add_compose_arguments(parser)
    args = parser.parse_args(argv)
    try:
        dump = args.dump or latest_dump(args.latest)
        report = drill(compose_from(args), dump)
    except (ToolError, OSError) as failed:
        print(f"restore drill: {failed}", file=sys.stderr)
        return 1
    print(f"restore drill passed: {dump} restores at migration {report.revision}")
    for table, rows in sorted(report.row_counts.items()):
        print(f"  {table:32} {rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

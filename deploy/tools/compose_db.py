"""What backup.py and restore.py share. Stdlib only.

Both tools reach the database the way an operator would: `docker compose exec -T
db`, running the PostgreSQL client that ships in the database image. The platform
image carries no PostgreSQL client, which is why these are host tools rather than
`meridian` subcommands (D-115).

**No credential crosses the command line.** Every command inside the container
reads `$POSTGRES_USER` and `$POSTGRES_DB` from the container's own environment and
connects over its local socket, so neither tool needs the password, and nothing
sensitive shows up in `ps` on the host.

Everything that decides something is a pure function — the commands built, the
facts parsed, the manifest checked — so tests/unit/test_backup_tools.py can pin it
without Docker. Only `run_sql` and the callers' streaming touch a process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path

MANIFEST_FORMAT = "meridian-backup/1"
CHUNK_BYTES = 1 << 20

# The deployment's database and the maintenance one. Single-quoted into `sh -c`,
# so the variables expand inside the container, from the container's environment.
_PSQL = 'psql -X -q -t -A -v ON_ERROR_STOP=1 -U "$POSTGRES_USER"'
_ON_DEPLOYMENT_DATABASE = '-d "$POSTGRES_DB"'
_ON_MAINTENANCE_DATABASE = '-d postgres -v db="$POSTGRES_DB"'

FACTS_SQL = (
    "select"
    " (select extversion from pg_extension where extname = 'timescaledb'),"
    " (select version_num from alembic_version),"
    " current_setting('server_version');"
)
"""One row: TimescaleDB version, alembic revision, server version.

Reading `alembic_version` fails on a database no migration has touched, which is
the right answer: there is nothing there worth backing up."""

AVAILABLE_TIMESCALEDB_SQL = (
    "select default_version from pg_available_extensions where name = 'timescaledb';"
)
"""The version `create extension` would install into a recreated database."""


class ToolError(Exception):
    """A step failed or was refused; the message is what the operator reads."""


@dataclass(frozen=True, slots=True)
class Compose:
    """How to address the deployment's compose project."""

    file: str
    project: str | None = None
    env_file: str | None = None

    def command(self, *args: str) -> list[str]:
        """`docker compose` with this project's options, then `args`."""
        prefix = ["docker", "compose", "-f", self.file]
        if self.project:
            prefix += ["-p", self.project]
        if self.env_file:
            prefix += ["--env-file", self.env_file]
        return [*prefix, *args]

    def in_db(self, script: str) -> list[str]:
        """Run a shell `script` inside the `db` container, stdin attached."""
        return self.command("exec", "-T", "db", "sh", "-c", script)

    def psql(self, *, maintenance: bool = False) -> list[str]:
        """psql reading SQL from stdin, on the deployment's or maintenance database.

        On the maintenance database the deployment's name is the psql variable
        `db`, so SQL can quote it as `:"db"` instead of splicing it into text.
        """
        target = _ON_MAINTENANCE_DATABASE if maintenance else _ON_DEPLOYMENT_DATABASE
        return self.in_db(f"{_PSQL} {target}")


def add_compose_arguments(parser: argparse.ArgumentParser) -> None:
    """The three options both tools take to find the compose project."""
    parser.add_argument(
        "--compose-file",
        default="deploy/docker-compose.yml",
        help="the compose file the deployment was started with",
    )
    parser.add_argument(
        "--project-name", default=None, help="compose project name, if not default"
    )
    parser.add_argument(
        "--env-file", default=None, help="the env file the deployment was started with"
    )


def compose_from(args: argparse.Namespace) -> Compose:
    """The :class:`Compose` the parsed options describe."""
    return Compose(args.compose_file, args.project_name, args.env_file)


def run_sql(compose: Compose, sql: str, *, maintenance: bool = False) -> str:
    """Run `sql` through psql in the `db` container and return its output.

    Raises:
        ToolError: psql, or `docker compose` in front of it, failed.
    """
    result = subprocess.run(
        compose.psql(maintenance=maintenance),
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit status {result.returncode}"
        raise ToolError(f"psql failed: {message}")
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class Manifest:
    """What a backup file is, written beside it as JSON."""

    sha256: str
    size_bytes: int
    timescaledb_version: str
    alembic_revision: str
    postgres_version: str
    created_at: str
    format: str = MANIFEST_FORMAT


def parse_facts(output: str) -> tuple[str, str, str]:
    """`FACTS_SQL`'s one row as (timescaledb, alembic revision, server version).

    Raises:
        ToolError: A value is missing — no TimescaleDB extension, or an empty
            `alembic_version` table.
    """
    values = output.strip().split("|")
    if len(values) != 3 or not all(values):
        raise ToolError(
            f"expected TimescaleDB version, alembic revision and server version; "
            f"the database answered {output.strip()!r}"
        )
    timescaledb, revision, server = values
    return timescaledb, revision, server


def manifest_path(dump: Path) -> Path:
    """Where the manifest for `dump` lives: beside it, with a suffix added."""
    return dump.with_name(dump.name + ".manifest.json")


def write_manifest(manifest: Manifest, path: Path) -> None:
    """Write `manifest` as indented JSON."""
    path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> Manifest:
    """Read a manifest written by :func:`write_manifest`.

    Raises:
        ToolError: The file is missing, is not JSON, is another format, or lacks
            a field. A restore without a trustworthy manifest is refused, not
            attempted on hope.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ToolError(
            f"no manifest at {path}; restore refuses a bare dump"
        ) from missing
    except json.JSONDecodeError as broken:
        raise ToolError(f"manifest {path} is not JSON: {broken}") from broken

    if not isinstance(document, dict) or document.get("format") != MANIFEST_FORMAT:
        raise ToolError(f"manifest {path} is not a {MANIFEST_FORMAT} manifest")
    names = [field.name for field in fields(Manifest)]
    missing_names = [name for name in names if name not in document]
    if missing_names:
        raise ToolError(f"manifest {path} lacks {', '.join(missing_names)}")
    return Manifest(**{name: document[name] for name in names})


def sha256_of(path: Path) -> str:
    """The file's sha256, read in chunks so a large dump is never held in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()

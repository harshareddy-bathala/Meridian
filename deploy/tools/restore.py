"""Restore a Meridian database from a backup.py dump. Stdlib only.

**This replaces the deployment's database.** Everything written since the backup
— heartbeats, observations, assignments, invites — is gone afterwards. It asks for
confirmation unless given `--yes`.

It refuses before touching anything when (D-115):

- the dump's manifest is missing or unreadable;
- the dump's sha256 differs from the manifest's — a truncated copy or the wrong file;
- the TimescaleDB version the database image would install differs from the one
  the dump was taken from. Upstream does not support restoring across extension
  versions, and the failure shows up later as a broken hypertable, not at restore.

Then, in order:

1. stop `api` and `jobs`, so nothing writes to a half-restored database;
2. drop and recreate the database;
3. `timescaledb_pre_restore()`, `pg_restore`, `timescaledb_post_restore()`;
4. run `migrate`, which brings a dump from an older release up to this code's head;
5. start again whichever of `api` and `jobs` was running, and wait for `/healthz`.

A failure after step 2 leaves `api` and `jobs` stopped on purpose: starting them
against a partial database would write new rows into it. The message says which
step failed; rerunning the restore starts from step 1 again.

Run from the repository root:

    python deploy/tools/restore.py backups/meridian-2026-09-14.dump
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
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

STOPPED_SERVICES = ("api", "jobs")
HEALTHZ_TIMEOUT_S = 120
HEALTHZ_POLL_S = 2

RECREATE_SQL = 'drop database if exists :"db" with (force);\ncreate database :"db";\n'
PRE_RESTORE_SQL = (
    "create extension if not exists timescaledb;\nselect timescaledb_pre_restore();\n"
)
POST_RESTORE_SQL = "select timescaledb_post_restore();\n"
RESTORE_SCRIPT = 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error'


def judge_restore(manifest: Manifest, sha256: str, available: str) -> str | None:
    """Why this dump must not be restored here, or ``None`` when it may be.

    Args:
        manifest: The dump's manifest.
        sha256: The dump file's sha256 as read now.
        available: The TimescaleDB version the database image would install.
    """
    if sha256 != manifest.sha256:
        return (
            f"the dump's sha256 is {sha256}, the manifest says {manifest.sha256}: "
            "the file is not the one that was backed up"
        )
    if available != manifest.timescaledb_version:
        return (
            f"the dump is from TimescaleDB {manifest.timescaledb_version} and this "
            f"database image installs {available}; restore it with the image it "
            "was taken from, then upgrade"
        )
    return None


def restore_command(compose: Compose) -> list[str]:
    """The command that reads a custom-format dump on stdin into the database."""
    return compose.in_db(RESTORE_SCRIPT)


def running_services(compose: Compose) -> tuple[str, ...]:
    """Which of :data:`STOPPED_SERVICES` are running now, to start them again."""
    result = subprocess.run(
        compose.command("ps", "--services", "--status", "running"),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ToolError(f"docker compose ps failed: {result.stderr.strip()}")
    running = set(result.stdout.split())
    return tuple(service for service in STOPPED_SERVICES if service in running)


def run_step(description: str, command: list[str], stdin: Path | None = None) -> None:
    """Run one step with its output shown, raising if it fails.

    Every progress line is flushed: the child writes straight to the terminal,
    and a buffered line would otherwise land after the output it introduces.
    """
    print(f"-> {description}", flush=True)
    if stdin is None:
        result = subprocess.run(command, check=False)
    else:
        with stdin.open("rb") as handle:
            result = subprocess.run(command, stdin=handle, check=False)
    if result.returncode != 0:
        raise ToolError(f"{description} failed (exit {result.returncode})")


def wait_for_healthz(base_url: str) -> None:
    """Poll `/healthz` until it answers 200, or give up after the timeout."""
    deadline = time.monotonic() + HEALTHZ_TIMEOUT_S
    url = f"{base_url.rstrip('/')}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=HEALTHZ_POLL_S) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(HEALTHZ_POLL_S)
    raise ToolError(f"{url} did not answer 200 within {HEALTHZ_TIMEOUT_S}s")


def confirm(dump: Path, manifest: Manifest, *, assume_yes: bool) -> None:
    """Ask before replacing the database, unless `--yes` was given."""
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise ToolError("not a terminal and --yes not given; nothing was changed")
    print(
        f"This replaces the deployment's database with {dump}, taken "
        f"{manifest.created_at} at revision {manifest.alembic_revision}.\n"
        "Everything written since then is lost."
    )
    if input("Type 'restore' to continue: ").strip() != "restore":
        raise ToolError("not confirmed; nothing was changed")


def restore(compose: Compose, dump: Path, *, assume_yes: bool, base_url: str) -> None:
    """Check `dump`, then replace the deployment's database with it."""
    manifest = read_manifest(manifest_path(dump))
    available = run_sql(compose, AVAILABLE_TIMESCALEDB_SQL, maintenance=True)
    refusal = judge_restore(manifest, sha256_of(dump), available)
    if refusal is not None:
        raise ToolError(refusal)
    confirm(dump, manifest, assume_yes=assume_yes)

    restart = running_services(compose)
    run_step("stop api and jobs", compose.command("stop", *STOPPED_SERVICES))
    print("-> recreate the database", flush=True)
    run_sql(compose, RECREATE_SQL, maintenance=True)
    run_sql(compose, PRE_RESTORE_SQL)
    run_step("pg_restore", restore_command(compose), stdin=dump)
    run_sql(compose, POST_RESTORE_SQL)
    run_step("migrate to this code's head", compose.command("run", "--rm", "migrate"))
    if restart:
        run_step(
            "start " + " and ".join(restart), compose.command("up", "-d", *restart)
        )
    if "api" in restart:
        print("-> wait for /healthz", flush=True)
        wait_for_healthz(base_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dump", type=Path, help="a dump written by backup.py")
    parser.add_argument(
        "--yes", action="store_true", help="do not ask before replacing the database"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="where the API answers once restarted, for the /healthz wait",
    )
    add_compose_arguments(parser)
    args = parser.parse_args(argv)

    try:
        restore(
            compose_from(args), args.dump, assume_yes=args.yes, base_url=args.base_url
        )
    except ToolError as refused:
        print(f"restore: {refused}", file=sys.stderr)
        return 1

    print(f"restored {args.dump}; run `meridian db status` to confirm the revision")
    return 0


if __name__ == "__main__":
    sys.exit(main())

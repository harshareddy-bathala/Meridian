"""Back up a running Meridian database to a file and a manifest. Stdlib only.

Streams `pg_dump --format=custom` out of the `db` container into `--out`, hashing
as it writes, then writes `<out>.manifest.json` beside it with the file's sha256,
the TimescaleDB version, the alembic revision, the server version and the time.
restore.py checks the first two before it touches anything (D-115).

The API keeps running. `pg_dump` reads one consistent snapshot, so a heartbeat
arriving mid-dump is simply in the next backup.

**The manifest also says how many rows every table held, in that same snapshot**
(D-286). A session is opened first, in a transaction that exports its snapshot
and counts every table; `pg_dump --snapshot` then dumps exactly what was
counted, however much is written meanwhile. restore.py compares what it restored
with those counts, so a restore can be checked on a machine that has never seen
the database the dump came from.

**It does not take the ingest raw store**, and it says so on every run. That tree
holds external artefacts exactly as they were retrieved, it is not in Postgres,
and it is the one thing here that cannot be recreated without going back to a
source that may have withdrawn it (D-141). A backup that quietly omitted it
would be discovered at the worst possible moment. **Nor does it take the dataset
snapshots** under `data/datasets`: a raw snapshot is taken at the moment it is
exported and cannot be taken again (D-144), so it is named on every run too.

The dump is written to `<out>.partial` and renamed only once `pg_dump` has exited
cleanly, so a file at `--out` is always a complete dump — an interrupted backup
leaves a `.partial` behind, never a truncated file that looks finished.

Run from the repository root:

    python deploy/tools/backup.py --out backups/meridian-2026-09-14.dump

A schedule and retention are Stage 23's.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from compose_db import (
    CHUNK_BYTES,
    COUNTS_SQL,
    FACTS_SQL,
    Compose,
    Manifest,
    ToolError,
    add_compose_arguments,
    compose_from,
    manifest_path,
    parse_counts,
    parse_facts,
    run_sql,
    write_manifest,
)

DUMP_SCRIPT = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'

COUNTED = "counted"
SNAPSHOT_SQL = (
    "begin isolation level repeatable read;\n"
    "select pg_export_snapshot();\n" + COUNTS_SQL + f"select '{COUNTED}';\n"
)
"""Open a transaction, name its snapshot, count every table in it, and say when
that is done. The session is then left open: the snapshot lives only as long as
the transaction that exported it."""

SNAPSHOT_ID = re.compile(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+")
"""What `pg_export_snapshot()` returns. Checked before it is put into a shell
command: it comes from the database, and is about to become part of a script."""

RAW_STORE = Path("data/ingest/raw")
"""The ingest raw store's default root, which this tool never touches.

The documented default rather than a value read from `ingest.toml`: this is a
stdlib-only host tool (D-115) and cannot import `meridian_ingest` to ask. An
operator who moved the root still reads a true sentence, because the point of
the line is that *no* raw store is in the dump.
"""


def raw_store_note(root: Path = RAW_STORE) -> str:
    """One line naming what this backup did not take.

    Args:
        root: Where the raw store is expected to be.

    Returns:
        A sentence for the operator, saying whether that tree is there at all.
    """
    state = "" if root.is_dir() else " (nothing there)"
    return f"not in this dump   {root}{state} — the ingest raw store (D-141)"


DATASETS_ROOT = Path("data/datasets")
"""The default datasets root, which this tool never touches either.

The documented default for the same reason as :data:`RAW_STORE`: a host tool
cannot import the platform to ask where ``MERIDIAN_DATASETS_ROOT`` points.
"""


def datasets_note(root: Path = DATASETS_ROOT) -> str:
    """One line naming the dataset snapshots this backup did not take.

    Args:
        root: Where the datasets root is expected to be.

    Returns:
        A sentence for the operator, saying whether that tree is there at all.
    """
    state = "" if root.is_dir() else " (nothing there)"
    return f"not in this dump   {root}{state} — dataset snapshots (D-144)"


def dump_command(compose: Compose, snapshot: str = "") -> list[str]:
    """The command whose stdout is the custom-format dump.

    Args:
        compose: The deployment.
        snapshot: An exported snapshot to dump from, so the dump holds exactly
            the rows counted in it. Empty for `pg_dump`'s own.
    """
    if not snapshot:
        return compose.in_db(DUMP_SCRIPT)
    if not SNAPSHOT_ID.fullmatch(snapshot):
        raise ToolError(f"the database named its snapshot {snapshot!r}")
    return compose.in_db(f"{DUMP_SCRIPT} --snapshot={snapshot}")


def read_snapshot(lines: list[str]) -> tuple[str, dict[str, int]]:
    """`SNAPSHOT_SQL`'s output, up to its last line: the id, then the counts.

    Raises:
        ToolError: Nothing came back, or no table was counted.
    """
    answered = [line.strip() for line in lines if line.strip()]
    if not answered:
        raise ToolError("the database exported no snapshot")
    snapshot, *counted = answered
    row_counts = parse_counts("\n".join(counted))
    if not row_counts:
        raise ToolError("the database counted no table; there is nothing to back up")
    return snapshot, row_counts


@contextmanager
def held_snapshot(compose: Compose) -> Iterator[tuple[str, dict[str, int]]]:
    """A snapshot held open on the database, and every table's rows in it.

    Raises:
        ToolError: The session ended before it had counted. psql's own message
            has already gone to stderr.
    """
    with subprocess.Popen(
        compose.psql(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as session:
        assert session.stdin is not None and session.stdout is not None
        session.stdin.write(SNAPSHOT_SQL)
        session.stdin.flush()
        lines = []
        for line in session.stdout:
            if line.strip() == COUNTED:
                break
            lines.append(line)
        else:
            raise ToolError("the database closed the session before counting its rows")
        try:
            yield read_snapshot(lines)
        finally:
            # End of input ends psql, and with it the transaction and snapshot.
            session.stdin.close()


def refuse_overwrite(out: Path) -> None:
    """Refuse when the dump, its manifest or a partial one is already there.

    Raises:
        ToolError: Something is at one of the three paths. A backup never
            replaces an older one, because the older one may be the good one.
    """
    partial = out.with_name(out.name + ".partial")
    for path in (out, manifest_path(out), partial):
        if path.exists():
            raise ToolError(f"{path} already exists; choose another --out")


def stream_dump(compose: Compose, out: Path, snapshot: str = "") -> tuple[str, int]:
    """Write the dump to `out`, returning its sha256 and size.

    Raises:
        ToolError: `pg_dump` exited non-zero. Its own message has already gone
            to stderr; the partial file is left for inspection.
    """
    partial = out.with_name(out.name + ".partial")
    digest = hashlib.sha256()
    size = 0
    with (
        partial.open("wb") as handle,
        subprocess.Popen(
            dump_command(compose, snapshot), stdout=subprocess.PIPE
        ) as dump,
    ):
        assert dump.stdout is not None
        while chunk := dump.stdout.read(CHUNK_BYTES):
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if dump.returncode != 0:
        raise ToolError(f"pg_dump exited {dump.returncode}; {partial} is incomplete")
    partial.rename(out)
    return digest.hexdigest(), size


def backup(compose: Compose, out: Path) -> Manifest:
    """Dump the deployment's database to `out` and write its manifest."""
    refuse_overwrite(out)
    timescaledb, revision, server = parse_facts(run_sql(compose, FACTS_SQL))
    out.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    with held_snapshot(compose) as (snapshot, row_counts):
        sha256, size = stream_dump(compose, out, snapshot)
    manifest = Manifest(
        sha256=sha256,
        size_bytes=size,
        timescaledb_version=timescaledb,
        alembic_revision=revision,
        postgres_version=server,
        created_at=created_at,
        row_counts=row_counts,
    )
    write_manifest(manifest, manifest_path(out))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="the dump to write")
    add_compose_arguments(parser)
    args = parser.parse_args(argv)

    try:
        manifest = backup(compose_from(args), args.out)
    except ToolError as refused:
        print(f"backup: {refused}", file=sys.stderr)
        return 1

    print(f"wrote {args.out} ({manifest.size_bytes} bytes)")
    print(f"  sha256               {manifest.sha256}")
    print(f"  alembic revision     {manifest.alembic_revision}")
    print(f"  timescaledb version  {manifest.timescaledb_version}")
    counts = manifest.row_counts or {}
    print(f"  rows counted         {sum(counts.values())} in {len(counts)} tables")
    print(f"  manifest             {manifest_path(args.out)}")
    print(f"  {raw_store_note()}")
    print("  copy that tree yourself — docs/OPERATIONS.md § External archive ingest")
    print(f"  {datasets_note()}")
    print("  copy that tree yourself — docs/OPERATIONS.md § Dataset snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())

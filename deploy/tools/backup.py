"""Back up a running Meridian database to a file and a manifest. Stdlib only.

Streams `pg_dump --format=custom` out of the `db` container into `--out`, hashing
as it writes, then writes `<out>.manifest.json` beside it with the file's sha256,
the TimescaleDB version, the alembic revision, the server version and the time.
restore.py checks the first two before it touches anything (D-115).

The API keeps running. `pg_dump` reads one consistent snapshot, so a heartbeat
arriving mid-dump is simply in the next backup.

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
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from compose_db import (
    CHUNK_BYTES,
    FACTS_SQL,
    Compose,
    Manifest,
    ToolError,
    add_compose_arguments,
    compose_from,
    manifest_path,
    parse_facts,
    run_sql,
    write_manifest,
)

DUMP_SCRIPT = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'


def dump_command(compose: Compose) -> list[str]:
    """The command whose stdout is the custom-format dump."""
    return compose.in_db(DUMP_SCRIPT)


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


def stream_dump(compose: Compose, out: Path) -> tuple[str, int]:
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
        subprocess.Popen(dump_command(compose), stdout=subprocess.PIPE) as dump,
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
    sha256, size = stream_dump(compose, out)
    manifest = Manifest(
        sha256=sha256,
        size_bytes=size,
        timescaledb_version=timescaledb,
        alembic_revision=revision,
        postgres_version=server,
        created_at=created_at,
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
    print(f"  manifest             {manifest_path(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

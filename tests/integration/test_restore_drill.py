"""The restore drill, run for real against the test database (D-209).

``deploy/tools/restore_drill.py`` drives ``docker compose exec db`` on a
deployment. Here the same scripts run through a local shell instead, with libpq's
``PG*`` variables pointing at the test server, so the drill under test is the one
the weekly timer runs, minus Docker: a dump is taken with ``backup.py``, restored
into the scratch database, checked against its manifest, and the scratch
database dropped.

It needs ``pg_dump``, ``pg_restore`` and ``psql`` on the ``PATH``, at the server's
major version. Without them the test skips on a workstation and fails in CI,
where a skipped drill would read as a passed one.

Reference: docs/DECISIONS.md D-115, D-209.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from psycopg import conninfo

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
BINARIES = ("pg_dump", "pg_restore", "psql")


@pytest.fixture(scope="module")
def tools() -> Iterator[dict[str, ModuleType]]:
    missing = [b for b in BINARIES if shutil.which(b) is None]
    if missing:
        message = f"the restore drill needs {', '.join(missing)} on the PATH"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    sys.path.insert(0, str(TOOLS))
    try:
        loaded = {}
        for name in ("backup", "restore_drill"):
            spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            # Registered first: its dataclasses look their module up by name.
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
        loaded["compose_db"] = sys.modules["compose_db"]
        yield loaded
    finally:
        sys.path.remove(str(TOOLS))
        for name in ("compose_db", "restore", "backup", "restore_drill"):
            sys.modules.pop(name, None)


@pytest.fixture
def local(
    tools: dict[str, ModuleType], database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """A `Compose` whose `db` container is this shell, pointed at the test server."""
    info = conninfo.conninfo_to_dict(database_url)
    for variable, key in (
        ("POSTGRES_USER", "user"),
        ("POSTGRES_DB", "dbname"),
        ("PGHOST", "host"),
        ("PGPORT", "port"),
        ("PGPASSWORD", "password"),
    ):
        monkeypatch.setenv(variable, str(info.get(key, "")))

    class LocalDb(tools["compose_db"].Compose):  # type: ignore[misc, name-defined]
        def in_db(self, script: str) -> list[str]:
            return ["sh", "-c", script]

    return LocalDb("unused")


def _counts(conn: Any) -> dict[str, int]:
    tables = conn.execute(
        "select table_name from information_schema.tables"
        " where table_schema = 'public' and table_type = 'BASE TABLE'"
    ).fetchall()
    return {
        name: conn.execute(f'select count(*) from public."{name}"').fetchone()[0]
        for (name,) in tables
    }


def test_a_backup_restores_into_the_scratch_database_with_every_row(
    tools: dict[str, ModuleType], local: Any, conn: Any, tmp_path: Path
) -> None:
    dump = tmp_path / "meridian-drill.dump"
    manifest = tools["backup"].backup(local, dump)
    conn.commit()
    source = _counts(conn)

    report = tools["restore_drill"].drill(local, dump)

    assert report.revision == manifest.alembic_revision
    assert report.row_counts == source
    scratch = conn.execute(
        "select count(*) from pg_database where datname = 'meridian_restore_drill'"
    ).fetchone()
    assert scratch == (0,), "the scratch database is dropped afterwards"


def test_a_damaged_dump_is_refused_before_anything_is_restored(
    tools: dict[str, ModuleType], local: Any, tmp_path: Path
) -> None:
    """The control: the drill can fail, and fails on the same rule restore.py uses."""
    dump = tmp_path / "meridian-damaged.dump"
    tools["backup"].backup(local, dump)
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dump.write_bytes(bytes(data))

    with pytest.raises(tools["compose_db"].ToolError, match="sha256"):
        tools["restore_drill"].drill(local, dump)


def test_a_dump_restoring_to_another_migration_fails_the_drill(
    tools: dict[str, ModuleType],
) -> None:
    manifest = tools["compose_db"].Manifest(
        sha256="x",
        size_bytes=1,
        timescaledb_version="2.29.0",
        alembic_revision="0016",
        postgres_version="16",
        created_at="2026-09-28T02:47:00+00:00",
    )
    report = tools["restore_drill"].DrillReport(
        revision="0015", row_counts={"alembic_version": 1, "stations": 0}
    )

    assert "0015" in tools["restore_drill"].judge_drill(manifest, report)

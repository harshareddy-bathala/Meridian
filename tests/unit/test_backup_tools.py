"""The backup and restore tools' decisions, without Docker.

Both scripts drive `docker compose exec` against a running deployment, so their
effects are exercised by the round trip in CI rather than here. What they decide
can be pinned without a container: the commands they build, how they read the
database's answers, and — most of all — what restore refuses. A restore that went
ahead with the wrong file would replace a working database with a broken one, so
the refusals are the cases that matter.

Marked as a unit test by living in ``tests/unit``: no network, no Docker. Files
are written only under ``tmp_path``.

Reference: docs/DECISIONS.md D-115.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tools() -> Iterator[tuple[ModuleType, ModuleType, ModuleType]]:
    """compose_db, backup and restore, imported the way running them does.

    Running a script puts its directory on ``sys.path``, which is how the two
    tools find ``compose_db``; the fixture does the same, and undoes it.
    """
    sys.path.insert(0, str(TOOLS))
    try:
        backup, restore = _load("backup"), _load("restore")
        # The copy both tools imported, so `ToolError` is one class, not two.
        yield sys.modules["compose_db"], backup, restore
    finally:
        sys.path.remove(str(TOOLS))
        sys.modules.pop("compose_db", None)


@pytest.fixture
def compose_db(tools: tuple[ModuleType, ...]) -> ModuleType:
    return tools[0]


@pytest.fixture
def backup(tools: tuple[ModuleType, ...]) -> ModuleType:
    return tools[1]


@pytest.fixture
def restore(tools: tuple[ModuleType, ...]) -> ModuleType:
    return tools[2]


@pytest.fixture
def no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything tries to start a process."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a refusal must happen before any command runs")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def manifest_for(compose_db: ModuleType, dump: Path) -> object:
    return compose_db.Manifest(
        sha256=hashlib.sha256(dump.read_bytes()).hexdigest(),
        size_bytes=dump.stat().st_size,
        timescaledb_version="2.29.0",
        alembic_revision="0012",
        postgres_version="16.9",
        created_at="2026-09-14T06:00:00+00:00",
    )


def test_the_compose_prefix_carries_only_the_options_given(
    compose_db: ModuleType,
) -> None:
    bare = compose_db.Compose("deploy/docker-compose.yml")
    named = compose_db.Compose("c.yml", project="meridian", env_file=".env")

    assert bare.command("ps") == ["docker", "compose", "-f", bare.file, "ps"]
    assert named.command("ps") == [
        "docker", "compose", "-f", "c.yml", "-p", "meridian", "--env-file", ".env", "ps"
    ]  # fmt: skip


def test_no_credential_is_placed_on_the_host_command_line(
    compose_db: ModuleType, backup: ModuleType, restore: ModuleType
) -> None:
    """Every command reads user and database from inside the container.

    Anything on the host's argv is visible to every user of the host through
    ``ps``; the container's environment is not.
    """
    compose = compose_db.Compose("c.yml")
    commands = [
        backup.dump_command(compose),
        restore.restore_command(compose),
        compose.psql(),
        compose.psql(maintenance=True),
    ]

    for command in commands:
        assert command[:6] == ["docker", "compose", "-f", "c.yml", "exec", "-T"]
        script = command[-1]
        assert '"$POSTGRES_USER"' in script
        assert "PASSWORD" not in script


def test_the_dump_is_custom_format_so_restore_can_read_it_from_stdin(
    compose_db: ModuleType, backup: ModuleType
) -> None:
    assert "--format=custom" in backup.dump_command(compose_db.Compose("c.yml"))[-1]


def test_the_backup_names_the_raw_store_it_did_not_take(
    backup: ModuleType, tmp_path: Path
) -> None:
    """A dump is not the whole deployment, and the operator hears that at dump time.

    The ingest raw store is not in Postgres and cannot be recreated without
    going back to a source that may no longer serve it (D-141). A backup that
    silently omitted it would be discovered at the worst possible moment, so
    the tool says which tree it left behind and whether anything is in it.
    """
    present = tmp_path / "raw"
    present.mkdir()

    assert str(present) in backup.raw_store_note(present)
    assert "nothing there" not in backup.raw_store_note(present)
    assert "nothing there" in backup.raw_store_note(tmp_path / "absent")


def test_the_database_name_is_a_psql_variable_when_recreating(
    compose_db: ModuleType, restore: ModuleType
) -> None:
    """Quoted by psql as an identifier, never spliced into the SQL text."""
    assert (
        '-v db="$POSTGRES_DB"' in compose_db.Compose("c.yml").psql(maintenance=True)[-1]
    )
    assert ':"db"' in restore.RECREATE_SQL


def test_the_database_facts_are_parsed_in_order(compose_db: ModuleType) -> None:
    assert compose_db.parse_facts("2.29.0|0012|16.9\n") == ("2.29.0", "0012", "16.9")


@pytest.mark.parametrize("output", ["", "|0012|16.9", "2.29.0||16.9", "2.29.0|0012"])
def test_missing_facts_refuse_the_backup(compose_db: ModuleType, output: str) -> None:
    with pytest.raises(compose_db.ToolError):
        compose_db.parse_facts(output)


def test_a_manifest_reads_back_as_written(
    compose_db: ModuleType, tmp_path: Path
) -> None:
    dump = tmp_path / "m.dump"
    dump.write_bytes(b"PGDMP contents")
    manifest = manifest_for(compose_db, dump)

    compose_db.write_manifest(manifest, compose_db.manifest_path(dump))

    assert compose_db.read_manifest(tmp_path / "m.dump.manifest.json") == manifest
    assert compose_db.sha256_of(dump) == manifest.sha256


@pytest.mark.parametrize(
    "content",
    ["not json", '{"format": "something-else/1"}', '{"format": "meridian-backup/1"}'],
)
def test_an_untrustworthy_manifest_is_refused(
    compose_db: ModuleType, tmp_path: Path, content: str
) -> None:
    path = tmp_path / "m.dump.manifest.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(compose_db.ToolError):
        compose_db.read_manifest(path)


def test_a_restore_of_the_file_that_was_backed_up_is_allowed(
    compose_db: ModuleType, restore: ModuleType, tmp_path: Path
) -> None:
    dump = tmp_path / "m.dump"
    dump.write_bytes(b"PGDMP contents")
    manifest = manifest_for(compose_db, dump)

    assert restore.judge_restore(manifest, compose_db.sha256_of(dump), "2.29.0") is None


def test_a_changed_file_is_refused(
    compose_db: ModuleType, restore: ModuleType, tmp_path: Path
) -> None:
    dump = tmp_path / "m.dump"
    dump.write_bytes(b"PGDMP contents")
    manifest = manifest_for(compose_db, dump)
    dump.write_bytes(b"PGDMP truncat")

    refusal = restore.judge_restore(manifest, compose_db.sha256_of(dump), "2.29.0")

    assert refusal is not None
    assert "sha256" in refusal


def test_another_timescaledb_version_is_refused(
    compose_db: ModuleType, restore: ModuleType, tmp_path: Path
) -> None:
    dump = tmp_path / "m.dump"
    dump.write_bytes(b"PGDMP contents")
    manifest = replace(manifest_for(compose_db, dump), timescaledb_version="2.28.1")

    refusal = restore.judge_restore(manifest, manifest.sha256, "2.29.0")

    assert refusal is not None
    assert "2.28.1" in refusal
    assert "2.29.0" in refusal


@pytest.mark.usefixtures("no_processes")
def test_a_backup_never_replaces_an_existing_file(
    backup: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "old.dump"
    existing.write_bytes(b"the good one")

    assert backup.main(["--out", str(existing)]) == 1
    assert existing.read_bytes() == b"the good one"
    assert "already exists" in capsys.readouterr().err


@pytest.mark.usefixtures("no_processes")
def test_a_restore_without_a_manifest_changes_nothing(
    restore: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = tmp_path / "bare.dump"
    dump.write_bytes(b"PGDMP contents")

    assert restore.main([str(dump), "--yes"]) == 1
    assert "no manifest" in capsys.readouterr().err

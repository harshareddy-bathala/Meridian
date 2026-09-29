"""The least-privilege roles of D-207, logged in as and tried.

``meridian_api`` is what the API, the jobs process and the operator CLI connect
as. The claim is that it can do everything the platform does — read and write
rows, in plain tables and in TimescaleDB hypertables whose chunks it never
created — and nothing that changes the schema. Each refusal below is paired with
the owner succeeding at the same statement, so a refusal cannot be a statement
that fails for everybody.

Roles belong to the cluster, so this sets the real role names' passwords on the
test server, to values generated here. Every row written is rolled back.

Reference: docs/DECISIONS.md D-207.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import errors

from meridian.cli_db import run_db_roles
from meridian.store.database_roles import (
    API_LOGIN,
    READER_LOGIN,
    LoginPasswords,
    ensure_database_roles,
)


@pytest.fixture
def passwords(conn: Any) -> LoginPasswords:
    chosen = LoginPasswords(api=secrets.token_hex(16), reader=secrets.token_hex(16))
    ensure_database_roles(conn, chosen)
    return chosen


def _login(database_url: str, role: str, password: str) -> psycopg.Connection[Any]:
    info = psycopg.conninfo.conninfo_to_dict(database_url)
    info.update(user=role, password=password)
    return psycopg.connect(**info)


@pytest.fixture
def api(database_url: str, passwords: LoginPasswords) -> Iterator[Any]:
    with _login(database_url, API_LOGIN, passwords.api) as connection:
        yield connection


@pytest.fixture
def reader(database_url: str, passwords: LoginPasswords) -> Iterator[Any]:
    with _login(database_url, READER_LOGIN, passwords.reader) as connection:
        yield connection


DDL = [
    "create table stage23_probe (a int)",
    "alter table stations add column stage23_probe int",
    "drop table invite_tokens",
    "truncate invite_tokens",
    "create index stage23_probe on stations (name)",
    "create temporary table stage23_probe (a int)",
    "create schema stage23_probe",
    "create view stage23_probe as select 1",
    "create function stage23_probe() returns int language sql as 'select 1'",
]


@pytest.mark.parametrize("statement", DDL)
def test_the_api_role_cannot_change_the_schema(api: Any, statement: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        api.execute(statement)
    api.rollback()


@pytest.mark.parametrize("statement", DDL)
def test_the_owner_can_so_each_refusal_is_about_the_role(
    conn: Any, statement: str
) -> None:
    """The control: every statement above is one the owner may run."""
    with conn.transaction(force_rollback=True):
        conn.execute(statement)


def test_the_api_role_reads_and_writes_rows(api: Any) -> None:
    with api.transaction(force_rollback=True):
        api.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (secrets.token_bytes(32), "stage23-roles"),
        )
        api.execute("update invite_tokens set label = label where label = 'x'")
        api.execute("delete from invite_tokens where label = 'stage23-roles'")
        assert api.execute("select count(*) from stations").fetchone() is not None


def test_the_api_role_reads_every_hypertable_chunk(api: Any) -> None:
    """Chunks are TimescaleDB's tables, created later; the grant reaches them."""
    for table in ("heartbeats", "observations", "observations_current"):
        api.execute(f"select count(*) from {table}").fetchone()


def test_the_reader_reads_and_writes_nothing(reader: Any) -> None:
    assert reader.execute("select count(*) from stations").fetchone() is not None
    with pytest.raises(errors.InsufficientPrivilege):
        reader.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (secrets.token_bytes(32), "stage23-roles"),
        )


def test_a_table_created_later_by_the_owner_is_granted_by_default(
    conn: Any, api: Any
) -> None:
    """A migration adding a table must not need `meridian db roles` to be usable."""
    conn.execute("create table stage23_later (a int)")
    conn.commit()
    try:
        api.execute("insert into stage23_later values (1)")
        api.rollback()
    finally:
        conn.execute("drop table stage23_later")
        conn.commit()


def test_running_it_again_changes_the_password_and_nothing_else(
    conn: Any, database_url: str, passwords: LoginPasswords
) -> None:
    rotated = LoginPasswords(api=secrets.token_hex(16), reader=passwords.reader)

    ensure_database_roles(conn, rotated)

    with pytest.raises(psycopg.OperationalError):
        _login(database_url, API_LOGIN, passwords.api).close()
    with _login(database_url, API_LOGIN, rotated.api) as again:
        assert again.execute("select current_user").fetchone() == (API_LOGIN,)


def test_the_command_sets_the_roles_from_the_environment(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    chosen = secrets.token_hex(16)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("API_DATABASE_PASSWORD", chosen)
    monkeypatch.setenv("READER_DATABASE_PASSWORD", secrets.token_hex(16))

    assert run_db_roles() == 0

    assert chosen not in capsys.readouterr().out
    with _login(database_url, API_LOGIN, chosen) as connection:
        assert connection.execute("select 1").fetchone() == (1,)

"""Applying the migrations, as opposed to reading them.

``test_migrations.py`` asserts what the schema looks like once it is there. This
file asserts that getting it there works: an empty database reaches head, and
running the upgrade a second time changes nothing.

The second one is the interesting case. ``deploy/docker-compose.yml`` runs the
``migrate`` service on every ``docker compose up``, so the second, third and
hundredth run all execute this path. If it were not idempotent the ten-minute
bring-up requirement would hold exactly once per machine.

Each test builds its own scratch database, so nothing here depends on whether the
shared one has already been migrated, or on the order pytest happens to pick.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "deploy" / "alembic.ini"
HEAD_REVISION = "0006"


@pytest.fixture
def scratch_database(database_url: str) -> Iterator[str]:
    """An empty database, dropped afterwards whatever happens.

    ``autocommit`` because ``create database`` cannot run inside a transaction.
    """
    name = f"meridian_lifecycle_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(database_url, autocommit=True) as admin, admin.cursor() as cur:
        cur.execute(f'create database "{name}"')
    try:
        base, _, _ = database_url.rpartition("/")
        yield f"{base}/{name}"
    finally:
        with (
            psycopg.connect(database_url, autocommit=True) as admin,
            admin.cursor() as cur,
        ):
            cur.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s",
                (name,),
            )
            cur.execute(f'drop database if exists "{name}"')


def _upgrade_to_head(url: str) -> None:
    """Run `alembic upgrade head` the way the compose migrate service does."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _applied_revision(url: str) -> Any:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select version_num from alembic_version")
        row = cur.fetchone()
    return row[0] if row else None


def test_empty_database_reaches_head(scratch_database: str, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The clean-checkout path: nothing, then the whole schema."""
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to_head(scratch_database)

    assert _applied_revision(scratch_database) == HEAD_REVISION

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from information_schema.tables "
            "where table_schema = 'public' and table_type = 'BASE TABLE'"
        )
        row = cur.fetchone()
    assert row is not None and row[0] >= 10


def test_upgrade_head_twice_is_a_no_op(scratch_database: str, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Compose runs `migrate` on every `up`. The second run must do nothing.

    Alembic guarantees this at the revision level — it compares against
    `alembic_version` and skips what is already applied — so this asserts the
    guarantee holds for *our* revisions rather than re-testing Alembic. A
    revision that ran unconditional SQL outside its `upgrade()` would fail here.
    """
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to_head(scratch_database)
    first = _applied_revision(scratch_database)

    _upgrade_to_head(scratch_database)

    assert _applied_revision(scratch_database) == first == HEAD_REVISION


def test_generated_observation_id_matches_the_documented_formula(
    scratch_database: str,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """D-027: the id in the acknowledgement is derived, and derived *this* way.

    Two observations are **inserted and read back**, so what is checked is the
    generated column the API will actually return. Evaluating the expression as a
    standalone ``select`` would prove only that PostgreSQL can hash a string —
    the column could be missing its ``generated always as`` clause, or wired to
    the wrong arguments, and the assertion would still pass.

    The expected value is computed independently in Python. If the SQL and the
    documented formula ever diverge, a station's retry stops matching its own
    first submission, which is the failure the derivation exists to prevent.

    Revision 2 is inserted as well because the id must change with the revision:
    a derivation that ignored it would give a correction the same public id as the
    report it corrects, and ``superseded`` in MSP §4.4's acknowledgement would
    become unanswerable.
    """
    import hashlib

    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to_head(scratch_database)

    def expected(assignment_id: str, revision: int) -> str:
        digest = hashlib.sha256(f"{assignment_id}:{revision}".encode()).hexdigest()
        return f"ob_{digest[:12]}"

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            ("norad:1", "T"),
        )
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
            " token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, 0, 0, 0, %s, %s)",
            ("st_1", "T", "tests", bytes(32), bytes(32)),
        )
        for revision in (1, 2):
            cur.execute(
                "insert into observations (assignment_id, revision, started_at,"
                " ended_at,"
                " station_id, satellite_id, outcome, content_sha256)"
                " values (%s, %s, now(), now(), %s, %s, %s, %s)",
                ("as_44b2", revision, "st_1", "norad:1", "no_signal", bytes(32)),
            )
        conn.commit()

        cur.execute(
            "select revision, observation_id from observations"
            " where assignment_id = %s order by revision",
            ("as_44b2",),
        )
        rows = cur.fetchall()

    assert rows == [(1, expected("as_44b2", 1)), (2, expected("as_44b2", 2))]

    # The id printed in MSP §4.4's acknowledgement example, so the specification
    # cannot drift from the schema without this failing.
    assert rows[0][1] == "ob_05601bd09768"

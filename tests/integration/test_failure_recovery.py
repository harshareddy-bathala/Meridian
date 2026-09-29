"""Two failures recovered from by running the same thing again (D-211).

**A migration that fails part way.** ``deploy/migrations/env.py`` runs an upgrade
in one transaction, so a revision that fails leaves the database at the revision
it started from, with nothing of the failed one applied, and ``migrate`` exiting
non-zero keeps the API from starting. The test copies the migrations, adds a
revision that creates a table and then fails, and upgrades a scratch database
through it.

**The jobs process dying in the middle of scheduling.** Each task of a round runs
in its own transaction, committed only when the task finishes, so a process that
dies after the scheduler has written rows but before the commit leaves none of
them. The next round, after the restart, schedules exactly what an uninterrupted
round would have, and the one after that writes nothing, because both tasks are
idempotent over a horizon (D-063, D-066).

The other failures ``OPERATIONS.md`` § Failure recovery lists are each proven by
tests that already existed; that section names them.

Reference: docs/DECISIONS.md D-019, D-063, D-066, D-110, D-211.
"""

from __future__ import annotations

import logging.config
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from meridian.jobs.rounds import DatabaseRoundWork, RoundPlan, run_round
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.scheduler.schedule_config import ScheduleConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "deploy" / "migrations"
ALEMBIC_INI = REPO_ROOT / "deploy" / "alembic.ini"

BROKEN_REVISION = '''"""A revision that does some of its work and then fails."""

from alembic import op

revision = "9999"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create table stage23_half_applied (a int)")
    op.execute("select this_function_does_not_exist()")


def downgrade() -> None:
    op.execute("drop table stage23_half_applied")
'''


@pytest.fixture
def scratch_database(database_url: str) -> Iterator[str]:
    name = f"meridian_recovery_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    try:
        base, _, _ = database_url.rpartition("/")
        yield f"{base}/{name}"
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity"
                " where datname = %s",
                (name,),
            )
            admin.execute(f'drop database if exists "{name}"')


def _config(url: str, scripts: Path = MIGRATIONS) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(scripts))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _revision(url: str) -> str | None:
    with psycopg.connect(url) as conn:
        row = conn.execute("select version_num from alembic_version").fetchone()
    return row[0] if row else None


def _has_table(url: str, table: str) -> bool:
    with psycopg.connect(url) as conn:
        row = conn.execute("select to_regclass(%s) is not null", (table,)).fetchone()
    return bool(row and row[0])


def test_a_failed_migration_leaves_the_revision_it_started_from(
    scratch_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    # env.py applies alembic.ini's logging, whose fileConfig disables every
    # logger that already exists, so later tests' caplog would see nothing.
    monkeypatch.setattr(logging.config, "fileConfig", lambda *_, **__: None)
    command.upgrade(_config(scratch_database), "head")
    head = ScriptDirectory.from_config(_config(scratch_database)).get_current_head()
    broken = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, broken, ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "versions" / "9999_half_applied.py").write_text(
        BROKEN_REVISION.format(head=head)
    )

    with pytest.raises(Exception, match="this_function_does_not_exist"):
        command.upgrade(_config(scratch_database, broken), "head")

    assert _revision(scratch_database) == head
    assert not _has_table(scratch_database, "stage23_half_applied")
    # The recovery: the fixed code migrates cleanly from where it stopped.
    command.upgrade(_config(scratch_database), "head")
    assert _revision(scratch_database) == head


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def _assignments(conn: Any) -> int:
    return conn.execute("select count(*) from assignments").fetchone()[0]


def test_a_scheduler_that_dies_mid_round_leaves_nothing_and_recovers(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st_crash_recovery", simulated=True)
    schedule_rows.satellite()
    # A receiving chain and a downlink it can hear, so the pair is propagated.
    rollback.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg)"
        " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10)",
        (station,),
    )
    rollback.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
        " values ('norad:99970', 137100000, 'lrpt')"
    )
    now = datetime.now(UTC)
    plan = RoundPlan(horizon=timedelta(hours=24), config=ScheduleConfig())
    opened = {"count": 0}

    @contextmanager
    def dies_during_scheduling() -> Iterator[Any]:
        """Commits the generation; loses the schedule after it has written."""
        opened["count"] += 1
        with rollback.transaction():
            yield rollback
            if opened["count"] == 2:
                raise RuntimeError("the jobs process died before it committed")

    @contextmanager
    def healthy() -> Iterator[Any]:
        with rollback.transaction():
            yield rollback

    orbit = SkyfieldOrbitService()
    before = _assignments(rollback)

    crashed = run_round(
        DatabaseRoundWork(dies_during_scheduling, orbit, lambda: None), plan, now
    )
    after_crash = _assignments(rollback)
    restarted = run_round(DatabaseRoundWork(healthy, orbit, lambda: None), plan, now)
    again = run_round(DatabaseRoundWork(healthy, orbit, lambda: None), plan, now)

    assert crashed.generated is not None and crashed.generated.passes_stored > 0
    assert crashed.scheduled is None
    assert after_crash == before, "nothing the dead round wrote was committed"
    assert restarted.scheduled is not None and restarted.scheduled.scheduled > 0
    assert _assignments(rollback) == before + restarted.scheduled.scheduled
    assert again.scheduled is not None and again.scheduled.rows_written == 0

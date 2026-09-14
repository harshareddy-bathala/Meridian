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
HEAD_REVISION = "0015"
"""The newest revision, written out rather than read from the script directory.

Deriving it would make these tests assert that alembic agrees with itself. Pinned,
they fail the moment a migration lands — which is the point: whoever adds one is
told to come and look at the lifecycle tests rather than discovering later that
they have been passing vacuously. Bump this in the same commit as the migration.
"""


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


def _upgrade_to(url: str, revision: str) -> None:
    """Run `alembic upgrade <revision>`, stopping part way through the history."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def _applied_revision(url: str) -> Any:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select version_num from alembic_version")
        row = cur.fetchone()
    return row[0] if row else None


def test_empty_database_reaches_head(scratch_database: str, monkeypatch) -> None:
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


def test_upgrade_head_twice_is_a_no_op(scratch_database: str, monkeypatch) -> None:
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
    monkeypatch,
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


# Distinct because `stations.token_sha256` is unique from 0007 onward. Real
# hashes are 32 bytes; the length is irrelevant here and being explicit beats an
# escape sequence that renders as an invisible control character in the source.
TOKEN_HASH = bytes([1]) * 32
KEY_HASH = bytes([2]) * 32
SECOND_TOKEN_HASH = bytes([3]) * 32

INSERT_STATION = (
    "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
    " alt_m, token_sha256, registration_key_sha256)"
    " values (%s, %s, 'test', 12.9716, %s, 920, %s, %s)"
)


def test_0007_rewrites_a_longitude_the_old_range_allowed(
    scratch_database: str, monkeypatch
) -> None:
    """D-052: 200 degrees east becomes -160, rather than failing the migration.

    Migration 0007 narrows `stations.lon_deg` from -180..360 to -180..180. A
    CHECK is validated against existing rows when it is created, so a station
    stored under the old range would fail the migration outright and stop a
    deployment half way through — which is why the revision rewrites before it
    constrains.

    This steps to 0006, writes the row the old constraint allowed, and only then
    upgrades. Asserting the constraint alone would not catch a revision that
    added it without the rewrite: against an empty database, that revision
    passes.
    """
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to(scratch_database, "0006")

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute(INSERT_STATION, ("st-east", "East", 200.0, TOKEN_HASH, KEY_HASH))
        conn.commit()

    _upgrade_to_head(scratch_database)

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute("select lon_deg from stations where station_id = 'st-east'")
        row = cur.fetchone()

    assert row is not None
    # 200E and -160 are the same meridian, which is what makes the rewrite
    # lossless rather than a guess at what the operator meant.
    assert row[0] == -160.0


def test_0007_refuses_a_longitude_outside_iso_6709(
    scratch_database: str, monkeypatch
) -> None:
    """After 0007, the range azimuth uses is no longer storable as a longitude."""
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to_head(scratch_database)

    with (
        psycopg.connect(scratch_database) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        cur.execute(
            INSERT_STATION, ("st-bad", "Bad", 200.0, SECOND_TOKEN_HASH, KEY_HASH)
        )


def test_0014_backfills_a_station_registered_before_the_column_existed(
    scratch_database: str, monkeypatch
) -> None:
    """D-082: the column default *is* the backfill, and nothing else runs.

    Migration 0014 adds `location_precision_decimals` with `not null default 2`
    and no separate `update`. That is only correct if Postgres applies the
    default to rows already present — so this steps to 0013, writes a station the
    way registration did before MSP 0.2 defined the field, and only then upgrades.

    `test_empty_database_reaches_head` cannot prove this: 0014 runs against zero
    station rows there, so a revision that added the column *without* a default
    would pass it and leave every existing station null.

    The coordinates are asserted too. The declared precision governs publication
    only, and a migration that rounded the stored values while adding the column
    would be the exact failure D-082 exists to prevent — invisible until a pass
    was predicted for the wrong place.
    """
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to(scratch_database, "0013")

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute(
            INSERT_STATION, ("st-before", "Before", 77.594562, TOKEN_HASH, KEY_HASH)
        )
        conn.commit()

    _upgrade_to_head(scratch_database)

    with psycopg.connect(scratch_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select location_precision_decimals, lat_deg, lon_deg"
            " from stations where station_id = 'st-before'"
        )
        row = cur.fetchone()

    assert row is not None
    # 2 is the conservative end: roughly 1.1 km, the right campus rather than
    # the right building. It lands on operators who registered before the field
    # existed and therefore consented to nothing.
    assert row[0] == 2
    assert (row[1], row[2]) == (12.9716, 77.594562)


def test_0015_adds_reception_evidence_across_a_compressed_chunk(
    scratch_database: str, monkeypatch
) -> None:
    """D-119: 0015 lands on a hypertable that already has compressed chunks.

    `test_empty_database_reaches_head` runs 0015 against an empty table, where no
    chunk exists to be compressed — and a deployment that has run for a week has
    compressed chunks, because 0005's policy compresses anything older than seven
    days. So this stops at 0014, stores an observation old enough to fall in such
    a chunk, compresses it, and only then upgrades.

    Asserted afterwards: the old row still reads through `observations_current`
    with every new column null (not measured, never zero); the view shows the new
    columns at all; and the constraints both admit valid evidence and refuse a
    contradiction in that same compressed time range.
    """
    monkeypatch.setenv("DATABASE_URL", scratch_database)
    _upgrade_to(scratch_database, "0014")

    insert_observation = (
        "insert into observations (assignment_id, revision, started_at, ended_at,"
        " station_id, satellite_id, outcome, content_sha256)"
        " values (%s, 1, now() - interval '40 days', now() - interval '40 days',"
        " 'st-old', 'norad:1', 'no_signal', %s)"
    )
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute(
            "insert into satellites (satellite_id, name) values ('norad:1', 'T')"
        )
        conn.execute(INSERT_STATION, ("st-old", "Old", 77.5, TOKEN_HASH, KEY_HASH))
        conn.execute(insert_observation, ("as-old", bytes(32)))
        chunks = conn.execute(
            "select show_chunks('observations', older_than => interval '7 days')"
        ).fetchall()
        for (chunk,) in chunks:
            conn.execute("select compress_chunk(%s)", (chunk,))
        compressed = conn.execute(
            "select count(*) from timescaledb_information.chunks"
            " where hypertable_name = 'observations' and is_compressed"
        ).fetchone()
    assert compressed is not None and compressed[0] == 1

    _upgrade_to_head(scratch_database)

    with psycopg.connect(scratch_database, autocommit=True) as conn:
        old = conn.execute(
            "select outcome, noise_floor_dbfs, receiver_gain_db, snr_samples,"
            " decoder, decoder_version, frames_decoded, frames_failed"
            " from observations_current where assignment_id = 'as-old'"
        ).fetchone()
        assert old == ("no_signal", None, None, None, None, None, None, None)

        conn.execute(
            "insert into observations (assignment_id, revision, started_at, ended_at,"
            " station_id, satellite_id, outcome, content_sha256, noise_floor_dbfs,"
            " receiver_gain_db, snr_samples, decoder, frames_decoded)"
            " values ('as-new', 1, now() - interval '39 days',"
            " now() - interval '39 days', 'st-old', 'norad:1', 'no_signal', %s,"
            " -52.3, 32.8, '[]'::jsonb, 'satdump', 0)",
            (bytes(32),),
        )

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into observations (assignment_id, revision, started_at,"
                " ended_at, station_id, satellite_id, outcome, content_sha256,"
                " decoder, frames_decoded)"
                " values ('as-bad', 1, now() - interval '39 days',"
                " now() - interval '39 days', 'st-old', 'norad:1', 'no_signal', %s,"
                " 'satdump', 5)",
                (bytes(32),),
            )

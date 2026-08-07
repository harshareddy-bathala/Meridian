"""The connection pool, against real TimescaleDB.

Covers the three properties `meridian.store.pool` is built for: it opens without
waiting on the database, it hands out sessions already pinned to UTC, and it
reports an unreachable database as unreachable rather than raising.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from meridian.config import Settings, load_settings
from meridian.store.pool import (
    POOL_MAX_SIZE,
    is_database_reachable,
    open_pool,
)


@pytest.fixture
def settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("DATABASE_URL", database_url)
    return load_settings()


def test_the_pool_serves_a_query(settings: Settings) -> None:
    pool = open_pool(settings)
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("select 1")
            assert cur.fetchone() == (1,)
    finally:
        pool.close()


def test_sessions_are_pinned_to_utc(settings: Settings) -> None:
    """DATA-MODEL.md: "all timestamps UTC, no exceptions".

    A session inheriting the server's local zone returns timestamptz values
    converted into it, and every comparison downstream is then wrong by an offset
    that changes twice a year. The pool sets this per connection, so the property
    has to hold on a connection the pool handed out — not on one the test opened.
    """
    pool = open_pool(settings)
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("show timezone")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "UTC"
    finally:
        pool.close()


def test_a_reachable_database_reports_reachable(settings: Settings) -> None:
    pool = open_pool(settings)
    try:
        assert is_database_reachable(pool) is True
    finally:
        pool.close()


def test_an_unreachable_database_reports_false_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The health check must degrade, not 500.

    Port 1 is reserved and nothing listens on it, so this exercises the connect
    failure rather than a mocked one. `is_database_reachable` swallowing it is the
    difference between /healthz answering "database: unreachable" and /healthz
    itself being the second thing that is down.
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/meridian"
    )
    pool = open_pool(load_settings())
    try:
        assert is_database_reachable(pool) is False
    finally:
        pool.close()


def test_closing_a_pool_twice_is_safe(settings: Settings) -> None:
    """The lifespan closes in a `finally`, which can run after an earlier close."""
    pool = open_pool(settings)
    pool.close()
    pool.close()


def test_the_pool_is_bounded(settings: Settings) -> None:
    """POOL_MAX_SIZE is a Pi memory budget, not a default worth drifting from.

    PostgreSQL forks a backend per connection; a pool sized for a server is how
    the Pi runs out of RAM serving fifty stations it could have served with eight.
    """
    pool = open_pool(settings)
    try:
        assert pool.max_size == POOL_MAX_SIZE
    finally:
        pool.close()

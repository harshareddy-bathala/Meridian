"""Fixtures shared by the whole suite, and the directory-to-marker rule.

The database fixtures live here rather than in one test module because every
integration test needs the same connection and the same skip behaviour, and a
second copy of them is a second place for the skip logic to be wrong.

Tests run against real TimescaleDB, never SQLite. The schema uses ``timestamptz``,
arrays, ``CHECK`` constraints, generated columns and hypertables; a suite that
passes on SQLite says nothing about what runs on the Pi.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent

DIRECTORY_MARKERS = {
    "integration": "integration",
    "msp_conformance": "msp_conformance",
    "e2e": "e2e",
}
"""Which directory implies which marker.

``unit/`` is deliberately absent: it is the default and carries no marker, so
``-m "not integration and not e2e and not msp_conformance"`` selects it.
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply each directory's marker to every test collected beneath it.

    **A hook, not ``pytestmark`` in the directory's conftest.** ``pytestmark`` is
    honoured in test *modules* only; setting it in a ``conftest.py`` is silently
    ignored, which is the worst possible failure here — the marker looks applied,
    `-m e2e` selects nothing, and the e2e tests run inside the unit job instead.
    Verified by ``tests/unit/test_marker_wiring.py``, which exists because that
    mistake is invisible until a compose bring-up appears in a job that should
    take two seconds.

    Marking by directory rather than per module means a new test file cannot
    forget to declare what it needs.
    """
    for item in items:
        try:
            relative = Path(str(item.fspath)).resolve().relative_to(TESTS_ROOT)
        except ValueError:  # pragma: no cover — collected from outside tests/
            continue
        if not relative.parts:  # pragma: no cover
            continue
        marker = DIRECTORY_MARKERS.get(relative.parts[0])
        if marker is not None:
            item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture(scope="session")
def database_url() -> str:
    """The libpq URL for the test database, or skip.

    Normalised through ``meridian.config`` so a ``DATABASE_URL`` carrying the
    ``+psycopg`` suffix Alembic wants still works here — one variable, two
    consumers (D-033).
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("DATABASE_URL is not set; start TimescaleDB and export it")

    from meridian.config import libpq_url

    return libpq_url(raw)


@pytest.fixture(scope="session")
def conn(database_url: str) -> Iterator[Any]:
    """One session-scoped connection against the migrated schema."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(database_url) as connection:
        yield connection


@pytest.fixture
def scalar(conn: Any):
    """Run a query and return its first column, or ``None``."""

    def _scalar(sql: str, *args: object) -> Any:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
        return row[0] if row else None

    return _scalar

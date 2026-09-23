"""``SNAPSHOT_TABLES`` — the export's contract, checked without a database.

The integration tests prove what each query returns. These prove what every
query must be, whatever it returns: a file name the manifest accepts, an order
that makes the bytes repeatable, and no credential among the columns.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from meridian.datasets.manifest import FileEntry
from meridian.store.snapshot_reads import SNAPSHOT_TABLES, SnapshotScope, SnapshotTable

NAMES = [one.name for one in SNAPSHOT_TABLES]

NEVER_EXPORTED = (
    "token_sha256",
    "registration_key_sha256",
    "operator",
    "token_issued_at",
    "token_revoked_at",
)
"""Credentials, a column that may name a person, and current state no label
may use (D-143)."""


def test_every_table_is_a_file_the_manifest_accepts() -> None:
    for name in NAMES:
        FileEntry(name=f"{name}.jsonl", sha256=bytes(32), rows=0)


def test_no_table_is_listed_twice() -> None:
    assert len(set(NAMES)) == len(NAMES)


def test_the_snapshot_holds_what_d_143_lists() -> None:
    assert set(NAMES) == {
        "passes",
        "assignments",
        "observations",
        "heartbeats",
        "element_sets",
        "stations",
        "capabilities",
        "satellites",
        "transmitters",
        "archive_observations",
        "archive_stations",
        "ingest_records",
    }


@pytest.mark.parametrize("table", SNAPSHOT_TABLES, ids=NAMES)
def test_every_query_ends_in_a_fixed_order(table: SnapshotTable) -> None:
    """Rows in the same order every time are what make the bytes repeatable."""
    sql = table.sql
    assert re.search(r" order by [a-z_., ]+$", sql), sql[-60:]


@pytest.mark.parametrize("table", SNAPSHOT_TABLES, ids=NAMES)
def test_every_query_names_its_columns(table: SnapshotTable) -> None:
    """``select *`` would let a migration change a snapshot nobody decided to."""
    assert "*" not in table.sql


@pytest.mark.parametrize("column", NEVER_EXPORTED)
def test_no_query_reads_a_column_that_must_not_leave(column: str) -> None:
    for table in SNAPSHOT_TABLES:
        assert not re.search(rf"\b{column}\b", table.sql), table.name


@pytest.mark.parametrize("table", SNAPSHOT_TABLES, ids=NAMES)
def test_every_query_is_bounded_by_the_scope(table: SnapshotTable) -> None:
    """Only the two named parameters, and at least one of them."""
    sql = table.sql
    parameters = set(re.findall(r"%\((\w+)\)s", sql))
    assert parameters
    assert parameters <= {"since", "as_of"}


def test_a_scope_that_runs_backwards_is_refused() -> None:
    with pytest.raises(ValueError, match="after as_of"):
        SnapshotScope(
            since=datetime(2026, 9, 2, tzinfo=UTC),
            as_of=datetime(2026, 9, 1, tzinfo=UTC),
        )


def test_a_naive_scope_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SnapshotScope(since=datetime(2026, 9, 1), as_of=datetime(2026, 9, 2))  # noqa: DTZ001

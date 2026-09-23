"""``meridian.datasets.export`` — the database, frozen into a raw snapshot.

Marked ``integration`` by the directory hook. What matters here is what only
a database can show: the listening answers come from the registry and are
asked only about closed, scheduled assignments; the snapshot reads back whole;
and the transaction the command opens really is repeatable read and read only.

Reference: docs/DECISIONS.md D-143, D-144, D-145.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.export import (  # noqa: E402
    export_snapshot,
    snapshot_transaction,
)
from meridian.datasets.publish import read_directory  # noqa: E402
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
CLOSED = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    """The datasets root, made writable again afterwards so it can be removed."""
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


class RecordingRegistry:
    """Answers True for one assignment's station, and remembers every question."""

    def __init__(self) -> None:
        self.asked: list[ListeningQuery] = []

    def was_listening(self, query: ListeningQuery) -> bool:
        self.asked.append(query)
        return True


@pytest.fixture
def seeded(rollback: Any, schedule_rows: Any) -> dict[str, Any]:
    """A closed pass scheduled by A and skipped by B, and a pass still under way.

    The open pass is placed against the transaction's own ``now()``, which is
    the snapshot's ``as_of``, rather than the wall clock: the two differ by as
    long as the session's transaction has been open.
    """
    station = schedule_rows.station("st_export", simulated=False)
    element_set = schedule_rows.satellite()
    closed = schedule_rows.pass_(station, CLOSED, element_set_id=element_set)
    as_of = rollback.execute("select now()").fetchone()[0]
    future = schedule_rows.pass_(
        station, as_of - timedelta(minutes=1), element_set_id=element_set
    )
    schedule_rows.assignment("as_done", closed)
    schedule_rows.assignment(
        "as_skipped", closed, decision="skipped", model_config="B", state="expired"
    )
    schedule_rows.assignment("as_open", future)
    schedule_rows.observation("as_done", outcome="no_signal")
    return {"station": station}


def export(conn: Any, registry: Any, root: Path) -> Any:
    return export_snapshot(conn, registry, root=root, since=SINCE, created_at=CREATED)


def lines(directory: Any, name: str) -> list[dict[str, Any]]:
    return [json.loads(one) for one in directory.files[name].splitlines()]


# --- listening, frozen ---------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_only_closed_scheduled_assignments_are_asked_about(
    rollback: Any, root: Path
) -> None:
    """A skipped decision was never sent; an open window has nothing to confirm yet."""
    registry = RecordingRegistry()

    export(rollback, registry, root)

    (asked,) = registry.asked
    assert asked.station_id == "st_export"
    assert asked.satellite_id == "norad:99970"
    assert asked.centre_freq_hz == 137900000
    assert asked.mode == "lrpt"
    assert asked.window[0] == CLOSED


@pytest.mark.usefixtures("seeded")
def test_the_registrys_answer_is_stored_beside_what_was_asked(
    rollback: Any, root: Path
) -> None:
    published = export(rollback, RecordingRegistry(), root)

    (row,) = lines(read_directory(published.path), "listening.jsonl")

    assert row["assignment_id"] == "as_done"
    assert row["listening_confirmed"] is True
    assert published.manifest.counts["listening.confirmed"] == 1


def test_the_real_registry_decides_from_heartbeats(
    rollback: Any, root: Path, seeded: dict[str, Any]
) -> None:
    """A heartbeat naming the assignment, the satellite, the frequency and the mode."""
    rollback.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
        " listening_mode) values (%s, %s, %s, 'listening', 'as_done',"
        " 'norad:99970', 137900000, 'lrpt')",
        (
            seeded["station"],
            CLOSED + timedelta(minutes=3),
            CLOSED + timedelta(minutes=3),
        ),
    )
    registry = PsycopgRegistry(
        rollback, pepper="snapshot-test", recovery_window_s=3600, now_utc=CLOSED
    )

    published = export(rollback, registry, root)

    (row,) = lines(read_directory(published.path), "listening.jsonl")
    assert row["listening_confirmed"] is True


# --- the directory ------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_the_snapshot_holds_every_table_and_reads_back_whole(
    rollback: Any, root: Path
) -> None:
    published = export(rollback, RecordingRegistry(), root)

    directory = read_directory(published.path)

    assert "listening.jsonl" in directory.files
    assert "passes.jsonl" in directory.files
    assert directory.manifest.kind == "raw_snapshot"
    assert directory.manifest.since == SINCE
    assert published.path.parent == root / "snapshots"
    assert [one["assignment_id"] for one in lines(directory, "assignments.jsonl")] == [
        "as_done",
        "as_open",
        "as_skipped",
    ]


@pytest.mark.usefixtures("seeded")
def test_measured_and_simulated_are_counted_apart(rollback: Any, root: Path) -> None:
    published = export(rollback, RecordingRegistry(), root)

    counts = published.manifest.counts
    assert counts["passes.measured"] == 2
    assert counts["passes.simulated"] == 0
    assert counts["observations.measured"] == 1


@pytest.mark.usefixtures("seeded")
def test_exporting_twice_in_one_transaction_is_one_snapshot(
    rollback: Any, root: Path
) -> None:
    """One transaction, one instant, the same rows — so the same hash and name."""
    first = export(rollback, RecordingRegistry(), root)
    second = export_snapshot(
        rollback,
        RecordingRegistry(),
        root=root,
        since=SINCE,
        created_at=CREATED + timedelta(hours=1),
    )

    assert second.path == first.path
    assert second.written is False


# --- the archive denominator (D-150) ---------------------------------------------


def test_an_archive_station_s_passes_are_propagated_and_frozen(
    rollback: Any, root: Path, schedule_rows: Any
) -> None:
    """Our element set, the station's published location, real propagation."""
    satellite = "norad:25544"
    schedule_rows.element_set_at(satellite, SINCE - timedelta(days=1), "manual")
    station = schedule_rows.archive_reception(satellite, CLOSED)
    schedule_rows.archive_reception(satellite, CLOSED, location=None)

    published = export(rollback, RecordingRegistry(), root)

    passes = lines(read_directory(published.path), "archive_passes.jsonl")
    assert passes
    assert {one["archive_station_id"] for one in passes} == {station}
    assert {one["satellite_id"] for one in passes} == {satellite}
    assert all(one["aos"].startswith("2026-08-14T") for one in passes)
    counts = published.manifest.counts
    assert counts["archive_passes"] == len(passes)
    assert counts["archive_denominator.stations_without_location"] == 1
    assert counts["archive_denominator.stations_without_altitude"] == 1


# --- the transaction -----------------------------------------------------------


def test_the_export_transaction_is_repeatable_read_and_read_only(
    database_url: str,
) -> None:
    with psycopg.connect(database_url) as fresh, snapshot_transaction(fresh):
        isolation = fresh.execute("show transaction_isolation").fetchone()[0]
        read_only = fresh.execute("show transaction_read_only").fetchone()[0]
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), fresh.transaction():
            fresh.execute("create temporary table snapshot_write_probe (x int)")

    assert isolation == "repeatable read"
    assert read_only == "on"


def test_the_connection_is_left_as_it_was_found(database_url: str) -> None:
    with psycopg.connect(database_url) as fresh:
        before = (fresh.isolation_level, fresh.read_only)
        with snapshot_transaction(fresh):
            pass

        assert (fresh.isolation_level, fresh.read_only) == before

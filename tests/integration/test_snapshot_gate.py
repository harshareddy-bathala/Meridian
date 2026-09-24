"""Stage 15's gate, against a database: the dataset outlives the rows it came from.

Marked ``integration`` by the directory hook. A seeded database is exported to
a raw snapshot and labelled; then every source row is deleted, and the same
raw snapshot is labelled again into a fresh root. The hash is the same,
because labelling reads the snapshot and nothing else — which is what lets
someone who never had our database regenerate our numbers.

A second export after the delete is the control: its hash differs, so the
delete really happened and the equal label hashes are not an accident of
reading the same rows twice.

Reference: docs/DECISIONS.md D-143, D-144, D-145.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.export import export_snapshot, read_snapshot
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.registry.psycopg_registry import PsycopgRegistry

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
HEARD = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
SILENT = HEARD + timedelta(hours=2)
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


@pytest.fixture
def seeded(rollback: Any, schedule_rows: Any) -> str:
    """Two measured passes of one satellite: one decoded, one silent while listening."""
    station = schedule_rows.station("st_gate", simulated=False)
    element_set = schedule_rows.satellite()
    heard = schedule_rows.pass_(station, HEARD, element_set_id=element_set)
    silent = schedule_rows.pass_(station, SILENT, element_set_id=element_set)
    schedule_rows.assignment("as_heard", heard)
    schedule_rows.assignment("as_silent", silent)
    schedule_rows.observation("as_heard", outcome="decoded")
    schedule_rows.observation("as_silent", outcome="no_signal")
    rollback.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
        " listening_mode) values (%s, %s, %s, 'listening', 'as_silent',"
        " 'norad:99970', 137900000, 'lrpt')",
        (station, SILENT + timedelta(minutes=3), SILENT + timedelta(minutes=3)),
    )
    return station


def export(conn: Any, root: Path) -> Any:
    registry = PsycopgRegistry(
        conn, pepper="snapshot-gate", recovery_window_s=3600, now_utc=SILENT
    )
    read = read_snapshot(conn, registry, since=SINCE)
    return export_snapshot(read, root=root, created_at=CREATED)


def label(raw: Path, root: Path) -> Any:
    return build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=root, created_at=CREATED
    )


def delete_everything_from(conn: Any, station: str) -> None:
    """The source rows, gone: observations, heartbeats, assignments, passes."""
    for table in ("observations", "heartbeats", "assignments", "passes"):
        conn.execute(f"delete from {table} where station_id = %s", (station,))


def test_the_dataset_is_the_same_after_its_source_rows_are_gone(
    rollback: Any, root: Path, seeded: str
) -> None:
    raw = export(rollback, root / "db").path
    before = label(raw, root / "before")

    delete_everything_from(rollback, seeded)
    after = label(raw, root / "after")
    emptied = export(rollback, root / "emptied")

    before_hash = content_sha256(before.manifest).hex()
    assert content_sha256(after.manifest).hex() == before_hash
    assert after.path.name == before.path.name
    assert after.written
    assert (after.path / "labels.jsonl").read_bytes() == (
        before.path / "labels.jsonl"
    ).read_bytes()
    assert content_sha256(emptied.manifest) != content_sha256(
        read_directory(raw).manifest
    )
    assert emptied.manifest.counts["passes.measured"] == 0


def test_what_was_labelled_is_what_was_seeded(
    rollback: Any, root: Path, seeded: str
) -> None:
    """So the gate above compares two real datasets, not two empty ones."""
    raw = export(rollback, root / "db").path

    published = label(raw, root / "labelled")

    rows = [
        json.loads(one)
        for one in (published.path / "labels.jsonl").read_bytes().splitlines()
    ]
    assert {row["station_id"] for row in rows} == {seeded}
    assert sorted(row["label"] for row in rows) == [
        "confirmed_miss",
        "successful_reception",
    ]
    assert all(row["simulated"] is False for row in rows)

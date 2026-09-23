"""Fixtures the unit tests of Stage 15 share: a raw snapshot, built without a database.

A raw snapshot is files and a manifest, so the labelling side of the stage can
be tested end to end — publish, label, verify, the gate — with rows written by
hand. ``raw_snapshot`` publishes one exactly as the export would, through the
same writer, so what the labeller reads is what it will read in production.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, SourceEntry, content_sha256, file_entry
from meridian.datasets.publish import publish_directory

AS_OF = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
AOS = AS_OF - timedelta(days=3)

RAW_TABLES = (
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
    "listening",
    "archive_passes",
)
"""What an export writes: every snapshot table, the frozen listening answers,
and the archive stations' computed passes."""

SOURCE = SourceEntry(
    source_id="reference_archive",
    licence="CC-BY-4.0",
    terms_url="https://example.invalid/terms",
    attribution_entry="The reference adapter's fixtures are ours",
    records=1,
)

RawSnapshot = Callable[[Mapping[str, Sequence[Mapping[str, object]]]], Path]


def _pass(pass_id: int, station: str, *, simulated: bool = False) -> dict[str, object]:
    return {
        "id": pass_id,
        "satellite_id": "norad:57166",
        "station_id": station,
        "aos": AOS,
        "los": AOS + timedelta(minutes=11),
        "element_set_id": 1,
        "simulated": simulated,
    }


def _assignment(
    pass_id: int, station: str, *, simulated: bool = False
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{pass_id}",
        "pass_id": pass_id,
        "station_id": station,
        "start_at": AOS,
        "end_at": AOS + timedelta(minutes=11),
        "decision": "scheduled",
        "state": "reported",
        "model_config": "A",
        "simulated": simulated,
    }


def _observation(
    pass_id: int, outcome: str, *, simulated: bool = False
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{pass_id}",
        "revision": 1,
        "outcome": outcome,
        "simulated": simulated,
    }


WORLD: Mapping[str, Sequence[Mapping[str, object]]] = {
    "passes": [
        _pass(1, "st_a"),
        _pass(2, "st_b"),
        _pass(3, "st_sim", simulated=True),
    ],
    "assignments": [
        _assignment(1, "st_a"),
        _assignment(2, "st_b"),
        _assignment(3, "st_sim", simulated=True),
    ],
    "observations": [
        _observation(1, "decoded"),
        _observation(2, "no_signal"),
        _observation(3, "decoded", simulated=True),
    ],
    "element_sets": [
        {"id": 1, "satellite_id": "norad:57166", "epoch": AOS - timedelta(hours=6)},
    ],
    "heartbeats": [
        {"station_id": "st_b", "received_at": AOS + timedelta(minutes=2)},
    ],
    "listening": [
        {"assignment_id": "as_1", "listening_confirmed": True},
        {"assignment_id": "as_2", "listening_confirmed": True},
        {"assignment_id": "as_3", "listening_confirmed": True},
    ],
    "archive_observations": [
        {
            "archive_observation_id": 1,
            "archive_station_id": 1,
            "source_id": "reference_archive",
            "satellite_key": "norad:57166",
            "satellite_key_kind": "norad",
            "started_at": AOS + timedelta(hours=1),
            "archive_outcome": "no_data",
            "source_outcome": "nothing heard",
        },
    ],
}
"""Three passes of one satellite: one decoded, one confirmed silent (a miss,
because pass 1 heard the satellite), and one simulated — plus an archive row."""


@pytest.fixture
def world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:data:`WORLD`, for tests that cannot import from a conftest."""
    return WORLD


@pytest.fixture
def datasets_root(tmp_path: Path) -> Iterator[Path]:
    """A datasets root, made writable again afterwards so pytest can remove it."""
    root = tmp_path / "datasets"
    yield root
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def raw_snapshot(datasets_root: Path) -> RawSnapshot:
    """Publish a raw snapshot holding the given rows, every other table empty."""

    def publish(tables: Mapping[str, Sequence[Mapping[str, object]]]) -> Path:
        files = {
            f"{name}.jsonl": b"".join(
                canonical_line(row) for row in tables.get(name, ())
            )
            for name in RAW_TABLES
        }
        manifest = Manifest(
            kind="raw_snapshot",
            schema_revision="0016",
            since=SINCE,
            as_of=AS_OF,
            files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
            created_at=AS_OF,
            sources=(SOURCE,) if tables.get("archive_observations") else (),
        )
        name = f"20260923T060000Z-{content_sha256(manifest).hex()[:12]}"
        return publish_directory(
            datasets_root / "snapshots", name, manifest, files
        ).path

    return publish

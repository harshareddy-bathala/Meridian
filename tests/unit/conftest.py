"""Fixtures the unit tests of Stage 15 share: a raw snapshot, built without a database.

A raw snapshot is files and a manifest, so the labelling side of the stage can
be tested end to end — publish, label, verify, the gate — with rows written by
hand. ``raw_snapshot`` publishes one exactly as the export would, through the
same writer, so what the labeller reads is what it will read in production.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
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
        "max_elevation_deg": 40.0,
        "element_set_id": 1,
        "computed_at": AOS - timedelta(hours=5),
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


ARCHIVE_DAY = datetime(2026, 9, 10, tzinfo=UTC)


def _archive_pass(day: int, hour: int, peak: float) -> dict[str, object]:
    aos = ARCHIVE_DAY + timedelta(days=day, hours=hour)
    return {
        "archive_station_id": 7,
        "satellite_id": "norad:25544",
        "aos": aos,
        "los": aos + timedelta(minutes=10),
        "max_elevation_deg": peak,
    }


def _heard(n: int, day: int, hour: int, outcome: str) -> dict[str, object]:
    return {
        "archive_observation_id": n,
        "archive_station_id": 7,
        "source_id": "reference_archive",
        "satellite_key": "norad:25544",
        "satellite_key_kind": "norad",
        "started_at": ARCHIVE_DAY + timedelta(days=day, hours=hour, seconds=30),
        "archive_outcome": outcome,
        "source_outcome": outcome,
    }


ARCHIVE_WORLD: Mapping[str, Sequence[Mapping[str, object]]] = {
    **WORLD,
    "stations": [
        {"station_id": "st_a", "lon_deg": 77.6},
        {"station_id": "st_b", "lon_deg": 77.6},
    ],
    "archive_stations": [{"archive_station_id": 7, "lon_deg": -1.5}],
    "archive_passes": [
        _archive_pass(0, 1, 40.0),
        _archive_pass(0, 5, 40.0),
        _archive_pass(0, 9, 10.0),
        _archive_pass(1, 3, 40.0),
        _archive_pass(2, 3, 40.0),
    ],
    "archive_observations": [
        _heard(11, 0, 1, "decoded"),
        _heard(12, 0, 5, "no_data"),
        _heard(13, 2, 3, "unknown"),
    ],
}
""":data:`WORLD`, and one archive station over three days. Day 0: three
passes, two heard — one decoded, one ``no_data`` — and the 10° one not, so it
is 2/3 complete and its low pass has no support. Day 1: nothing heard, so
``inactive``. Day 2: its one pass heard, outcome ``unknown`` — attempted but
not usable. Every 40° pass was attempted, so that cell is certain."""


@pytest.fixture
def archive_world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:data:`ARCHIVE_WORLD`, for tests that cannot import from a conftest."""
    return ARCHIVE_WORLD


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


@dataclass
class NetworkGuard:
    """Refuses every database connection and socket, and remembers each attempt."""

    attempts: list[str] = field(default_factory=list)

    def refuse(self, what: str) -> OSError:
        self.attempts.append(what)
        return OSError(f"the gate test has no network; {what} was attempted")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    """Close every door a labelling run could use to reach a database."""
    guard = NetworkGuard()

    def refuse_psycopg(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("psycopg.connect")

    def refuse_socket(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("socket.connect")

    monkeypatch.setattr(psycopg, "connect", refuse_psycopg)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse_psycopg)
    monkeypatch.setattr(socket.socket, "connect", refuse_socket)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    yield guard

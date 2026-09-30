"""Stage 19's gate, against a database: every deferred table filled and read.

One station of each population goes through what a deployment does: an
observation reporting a floor and a product is ingested, the database is
exported and labelled, and the profiles are built. Then:

* every one of the four tables holds rows for the station — each producer ran;
* every row is labelled as its station is — the provenance policy holds, and
  the measured station is the positive control for the simulated one;
* the public API serves what was built — a consumer reads each.

The source half, including each clause's positive control, is
``tests/unit/test_deferred_storage_gate.py``.

Marked ``integration`` by the directory hook.

Reference: docs/DECISIONS.md D-173 to D-178.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("psycopg")

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.export import export_snapshot, read_snapshot
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.publish import read_directory
from meridian.observations.ingest import Submission, ingest
from meridian.profile_build import build_profiles
from meridian.registry.psycopg_registry import PsycopgRegistry

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
AOS = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
WATERFALL = "ab" * 32
MASK = '[{"az_deg": 0, "min_el_deg": 20}]'
TABLES = ("noise_measurements", "products", "horizon_profiles", "interference_profiles")


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _received(
    conn: Any, schedule_rows: Any, station: str, simulated: bool, element_set: int
) -> None:
    """A station with a mask receives one pass, reporting a floor and a product."""
    schedule_rows.station(station, simulated=simulated)
    conn.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg, horizon_mask_json)"
        " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10, %s)",
        (station, MASK),
    )
    assignment_id = f"as_{station}"
    schedule_rows.assignment(
        assignment_id, schedule_rows.pass_(station, AOS, element_set_id=element_set)
    )
    started, ended = conn.execute(
        "select start_at, end_at from assignments where assignment_id = %s",
        (assignment_id,),
    ).fetchone()
    ingest(
        conn,
        Submission(
            assignment_id=assignment_id,
            started_at=started,
            ended_at=ended,
            outcome="decoded",
            signal_detected=True,
            first_detection_at=started + timedelta(seconds=40),
            peak_snr_db=11.4,
            doppler_samples=None,
            products=(
                {
                    "kind": "waterfall",
                    "uri": f"station:products/{WATERFALL}",
                    "sha256": WATERFALL,
                    "size_bytes": 2048,
                },
            ),
            client_notes=None,
            noise_floor_dbfs=-52.3,
            receiver_gain_db=32.8,
        ),
        station_id=station,
    )


def _exported_and_built(conn: Any, root: Path) -> None:
    """Export, label and build, as an operator or the jobs service would."""
    registry = PsycopgRegistry(
        conn, pepper="deferred-gate", recovery_window_s=3600, now_utc=AOS
    )
    raw = export_snapshot(
        read_snapshot(conn, registry, since=SINCE), root=root, created_at=CREATED
    )
    build_evaluation_dataset(
        read_directory(raw.path), LabelConfig(), root=root, created_at=CREATED
    )
    build_profiles(conn, root)


@pytest.fixture
def stations(rollback: Any, schedule_rows: Any, root: Path) -> dict[str, bool]:
    both = {"st_gate_sim": True, "st_gate_real": False}
    element_set = schedule_rows.satellite()
    for station, simulated in both.items():
        _received(rollback, schedule_rows, station, simulated, element_set)
    _exported_and_built(rollback, root)
    return both


@pytest.mark.parametrize("table", TABLES)
def test_every_deferred_table_is_filled_and_labelled_as_its_station(
    rollback: Any, stations: dict[str, bool], table: str
) -> None:
    for station, simulated in stations.items():
        labels = [
            row[0]
            for row in rollback.execute(
                f"select simulated from {table} where station_id = %s",
                (station,),
            ).fetchall()
        ]
        assert labels, (table, station)
        assert set(labels) == {simulated}, (table, station)


def test_the_declared_and_the_learned_horizon_are_both_built(
    rollback: Any, stations: dict[str, bool]
) -> None:
    sources = {
        row[0]
        for row in rollback.execute(
            "select distinct source from horizon_profiles where station_id = any(%s)",
            (list(stations),),
        ).fetchall()
    }

    assert sources == {"declared", "learned"}


def test_the_public_api_serves_what_was_built(
    rollback: Any, stations: dict[str, bool]
) -> None:
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        for station, simulated in stations.items():
            profiles = client.get(f"/api/v1/stations/{station}/profiles").json()
            observations = client.get(
                "/api/v1/observations", params={"station_id": station}
            ).json()["items"]

            assert profiles["simulated"] is simulated
            assert profiles["declared"] and profiles["learned"]
            assert profiles["interference"] is not None
            (reported,) = observations
            assert reported["products"] == [
                {"kind": "waterfall", "sha256": WATERFALL, "size_bytes": 2048}
            ]

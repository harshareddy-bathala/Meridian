"""``meridian.profile_build`` against a database and a real labelled dataset.

A seeded database is exported and labelled exactly as an operator would, and the
profiles are built from that dataset. Asserted: every station's profile is
written once per dataset; a second build writes nothing; a declared mask is
written when it changes and the earlier one stays; and every row carries its
station's provenance.

Marked ``integration`` by the directory hook.

Reference: docs/DECISIONS.md D-174, D-175.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.export import export_snapshot, read_snapshot
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.prediction.profiles import PROFILE_METHOD
from meridian.profile_build import build_profiles, declared_bins
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.store.profiles import HorizonBin

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
HEARD = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
MASK = '[{"az_deg": 90, "min_el_deg": 8}, {"az_deg": 315, "min_el_deg": 30}]'


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
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
    """One measured station that decoded one pass, with a declared mask."""
    station = schedule_rows.station("st_profiles", simulated=False)
    element_set = schedule_rows.satellite()
    heard = schedule_rows.pass_(station, HEARD, element_set_id=element_set)
    schedule_rows.assignment("as_profiles", heard)
    schedule_rows.observation("as_profiles", outcome="decoded")
    rollback.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg, horizon_mask_json)"
        " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10, %s)",
        (station, MASK),
    )
    return station


def labelled_dataset(conn: Any, root: Path) -> Any:
    """Export and label, as ``meridian snapshot export`` and ``label`` would."""
    registry = PsycopgRegistry(
        conn, pepper="profile-build", recovery_window_s=3600, now_utc=HEARD
    )
    raw = export_snapshot(
        read_snapshot(conn, registry, since=SINCE), root=root, created_at=CREATED
    )
    return build_evaluation_dataset(
        read_directory(raw.path), LabelConfig(), root=root, created_at=CREATED
    )


def learned_rows(conn: Any, table: str, station: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        f"select dataset_sha256, method, simulated from {table}"
        " where station_id = %s"
        + (" and source = 'learned'" if table == "horizon_profiles" else ""),
        (station,),
    ).fetchall()


def test_a_dataset_s_profiles_are_built_once(
    rollback: Any, root: Path, seeded: str
) -> None:
    dataset = labelled_dataset(rollback, root)

    first = build_profiles(rollback, root)
    second = build_profiles(rollback, root)

    sha256 = content_sha256(dataset.manifest)
    assert first.dataset == dataset.path
    assert (first.stations_built, first.horizon_rows, first.interference_rows) == (
        1,
        36,
        48,
    )
    assert second.already_built
    assert second.horizon_rows == 0
    horizon = learned_rows(rollback, "horizon_profiles", seeded)
    interference = learned_rows(rollback, "interference_profiles", seeded)
    assert len(horizon) == 36
    assert len(interference) == 48
    assert {(bytes(one[0]), one[1], one[2]) for one in horizon + interference} == {
        (sha256, PROFILE_METHOD, False)
    }


def test_a_declared_mask_is_written_when_it_changes_and_kept_after(
    rollback: Any, root: Path, seeded: str
) -> None:
    first = build_profiles(rollback, root)
    unchanged = build_profiles(rollback, root)
    rollback.execute(
        "update station_capabilities set horizon_mask_json ="
        ' \'[{"az_deg": 0, "min_el_deg": 12}]\'::jsonb where station_id = %s',
        (seeded,),
    )
    changed = build_profiles(rollback, root)

    assert (first.declared_written, unchanged.declared_written) == (1, 0)
    assert changed.declared_written == 1
    written = rollback.execute(
        "select built_at, azimuth_deg, azimuth_width_deg, min_elevation_deg"
        " from horizon_profiles where station_id = %s and source = 'declared'"
        " order by built_at, azimuth_deg",
        (seeded,),
    ).fetchall()
    builds = sorted({one[0] for one in written})
    assert len(builds) == 2
    assert [one[1:] for one in written if one[0] == builds[0]] == [
        (90.0, 225.0, 8.0),
        (315.0, 135.0, 30.0),
    ]
    assert [one[1:] for one in written if one[0] == builds[1]] == [(0.0, 360.0, 12.0)]


def test_with_no_labelled_dataset_only_the_declared_masks_are_written(
    rollback: Any, root: Path, seeded: str
) -> None:
    report = build_profiles(rollback, root / "empty")

    assert report.dataset is None
    assert report.declared_written == 1
    assert learned_rows(rollback, "horizon_profiles", seeded) == []


def test_a_declared_mask_reads_as_the_scheduler_reads_it() -> None:
    """D-175's step function, clockwise from each point, wrapping at north."""
    assert declared_bins(
        [{"az_deg": 315.0, "min_el_deg": 30.0}, {"az_deg": 90.0, "min_el_deg": 8.0}]
    ) == [HorizonBin(90.0, 225.0, 8.0, None), HorizonBin(315.0, 135.0, 30.0, None)]

"""Published values into ``environment_samples``: once, append-only, twice safely.

Stage 31's half of the load. The same path as the archive receptions — an
artefact is a record under a source with its terms, and what it normalises to
lands in one transaction — so the properties are the same ones: a second load
writes nothing, a revision is a second row published later, a tile yields no
row, and a value the source left blank is a row that says so.

Reference: docs/DECISIONS.md D-133, D-140, D-221, D-222.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.environment_samples import (  # noqa: E402 — after importorskip
    find_environment_samples_for_record,
)
from meridian_ingest.adapters import REGISTRY  # noqa: E402 — after importorskip
from meridian_ingest.adapters.protocol import FetchRequest  # noqa: E402
from meridian_ingest.extent import BoundingBox  # noqa: E402
from meridian_ingest.load import load_source  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "unit" / "public_fixtures"
FETCHED = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    root = tmp_path / "raw"
    yield RawStore(root)
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def kp(store: RawStore, publish_public: Any, file: str, at: datetime) -> Any:
    remote = REGISTRY["noaa_swpc_kp"].adapter.plan(FetchRequest())[0]
    return publish_public(store, "noaa_swpc_kp", remote, FIXTURES / file, at)


def test_a_published_index_loads_once_and_a_second_load_writes_nothing(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    kp(store, publish_public, "swpc-kp-2026-09-28.json", FETCHED)
    first = load_source(rollback, store, "noaa_swpc_kp")
    again = load_source(rollback, store, "noaa_swpc_kp")

    assert first.samples_written == 16
    assert again.samples_written == 0
    assert again.samples_already_held == 16
    assert again.wrote_nothing()


def test_a_follow_round_loads_only_what_arrived_since_the_last(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    kp(store, publish_public, "swpc-kp-2026-09-28.json", FETCHED)
    first = load_source(rollback, store, "noaa_swpc_kp", new_only=True)
    again = load_source(rollback, store, "noaa_swpc_kp", new_only=True)

    assert len(first.artefacts) == 1
    assert first.samples_written == 16
    assert again.artefacts == (), "an artefact already recorded is not re-read"


def test_a_blank_value_is_a_row_with_its_reason(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    kp(store, publish_public, "swpc-kp-2026-09-28.json", FETCHED)
    report = load_source(rollback, store, "noaa_swpc_kp")
    rows = find_environment_samples_for_record(rollback, report.artefacts[0].record_id)
    blank = [one for one in rows if one.value is None]
    assert [one.missing_reason for one in blank] == ["published without a value"]


def test_a_revision_is_a_second_row_published_later(
    rollback: Any, store: RawStore, publish_public: Any, scalar: Any
) -> None:
    kp(store, publish_public, "swpc-kp-2026-09-28.json", FETCHED)
    kp(store, publish_public, "swpc-kp-2026-09-29.json", FETCHED + timedelta(days=1))
    load_source(rollback, store, "noaa_swpc_kp")

    values = scalar(
        "select array_agg(value order by published_at) from environment_samples"
        " where source_id = 'noaa_swpc_kp' and series_key = %s",
        "kp:2026-09-28T12:00:00+00:00",
    )
    assert values == [1.0, 1.67]


def test_the_database_refuses_a_value_that_is_neither_there_nor_explained(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    kp(store, publish_public, "swpc-kp-2026-09-28.json", FETCHED)
    report = load_source(rollback, store, "noaa_swpc_kp")
    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        rollback.execute(
            "update environment_samples set missing_reason = null, value = null"
            " where record_id = %s",
            (report.artefacts[0].record_id,),
        )


def test_a_tile_is_recorded_and_nothing_is_derived_from_it(
    rollback: Any, store: RawStore, publish_public: Any, scalar: Any
) -> None:
    remote = REGISTRY["nasa_gibs"].adapter.plan(
        FetchRequest(bbox=BoundingBox(74.0, 11.5, 78.6, 18.5))
    )[0]
    publish_public(store, "nasa_gibs", remote, FIXTURES / "gibs-tile.jpg", FETCHED)
    report = load_source(rollback, store, "nasa_gibs")

    assert [one.skipped for one in report.artefacts] == ["tile"]
    assert (
        scalar("select count(*) from environment_samples where source_id = 'nasa_gibs'")
        == 0
    )
    assert (
        scalar(
            "select payload_kind from ingest_provenance where source_id = 'nasa_gibs'"
        )
        == "tile"
    )


def test_a_fetched_fire_day_carries_its_box_and_day_in_the_record(
    rollback: Any, store: RawStore, publish_public: Any, scalar: Any
) -> None:
    """Zero detections on a covered day must be distinguishable from no fetch."""
    remote = REGISTRY["nasa_firms"].adapter.plan(
        FetchRequest(
            bbox=BoundingBox(74.0, 11.5, 78.6, 18.5),
            since=datetime(2026, 9, 27, tzinfo=UTC),
            until=datetime(2026, 9, 28, tzinfo=UTC),
        )
    )[0]
    publish_public(store, "nasa_firms", remote, FIXTURES / "firms-empty.csv", FETCHED)
    load_source(rollback, store, "nasa_firms")

    assert (
        scalar(
            "select spatial_extent->>'west' from ingest_records"
            " where source_id = 'nasa_firms'"
        )
        == "74.0"
    )
    assert scalar(
        "select valid_from from ingest_records where source_id = 'nasa_firms'"
    ) == datetime(2026, 9, 27, tzinfo=UTC)

"""Another network's page into the archive tables: apart from ours, once, kept verbatim.

Stage 34's archive source through the same load as Stage 14's reference
archive (D-139, D-323). A page lands as archive stations and archive
observations, never in ``observations``; a second load writes nothing; a
re-fetch whose ratings moved is a second artefact, superseding the first.

Reference: docs/DECISIONS.md D-139, D-141, D-322, D-323.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.export import (  # noqa: E402 — after importorskip
    export_snapshot,
    read_snapshot,
)
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.store.element_sets import (  # noqa: E402
    find_current_element_sets,
    find_current_epochs,
    find_element_set_current_at,
)
from meridian.store.satellites import NewSatellite, insert_satellite  # noqa: E402
from meridian_ingest.adapters import REGISTRY  # noqa: E402
from meridian_ingest.adapters.protocol import FetchRequest  # noqa: E402
from meridian_ingest.load import load_source  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402

pytestmark = pytest.mark.integration

SOURCE = "satnogs_network"
PAGE = (
    Path(__file__).parents[1]
    / "unit"
    / "public_fixtures"
    / "satnogs-network-57166.json"
)
NOON = datetime(2026, 10, 7, 12, tzinfo=UTC)
EVENING = datetime(2026, 10, 7, 18, tzinfo=UTC)


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


def fetch(store: RawStore, publish_public: Any, file: Path, at: datetime) -> Any:
    (remote,) = REGISTRY[SOURCE].adapter.plan(
        FetchRequest(catalog_numbers=(57166,), since=NOON, until=EVENING)
    )
    return publish_public(store, SOURCE, remote, file, at)


def archived(conn: Any) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "select o.source_observation_id, o.archive_outcome, s.source_station_key"
            " from archive_observations o"
            " join archive_stations s using (archive_station_id)"
            " where o.source_id = %s order by o.source_observation_id, o.record_id",
            (SOURCE,),
        )
        return list(cur.fetchall())


def test_a_page_lands_in_the_archive_tables_and_nowhere_else(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    with rollback.cursor() as cur:
        cur.execute("select count(*) from observations")
        ours = cur.fetchone()[0]
    fetch(store, publish_public, PAGE, datetime(2026, 10, 9, 8, tzinfo=UTC))
    report = load_source(rollback, store, SOURCE)

    assert report.stations_written == 2
    assert report.receptions_written == 5
    assert archived(rollback) == [
        ("9001", "decoded", "101"),
        ("9002", "signal_no_decode", "102"),
        ("9003", "no_data", "101"),
        ("9004", "unknown", "101"),
        ("9005", "signal_no_decode", "102"),
    ]
    with rollback.cursor() as cur:
        cur.execute("select count(*) from observations")
        assert cur.fetchone()[0] == ours, "the archive never writes our own table"


def test_a_second_load_writes_nothing(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    fetch(store, publish_public, PAGE, datetime(2026, 10, 9, 8, tzinfo=UTC))
    load_source(rollback, store, SOURCE)
    again = load_source(rollback, store, SOURCE)

    assert again.wrote_nothing()
    assert again.receptions_already_held == 5


def test_a_rating_that_settled_later_is_a_second_artefact_superseding_the_first(
    rollback: Any, store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    """The lookback re-fetches a window as people vet it (D-141, D-323)."""
    fetch(store, publish_public, PAGE, datetime(2026, 10, 8, 8, tzinfo=UTC))
    page = json.loads(PAGE.read_text("utf-8"))
    for one in page:
        if one["id"] == 9004:
            one["status"] = "bad"
    vetted = tmp_path / "vetted.json"
    vetted.write_text(json.dumps(page), "utf-8")
    fetch(store, publish_public, vetted, datetime(2026, 10, 9, 8, tzinfo=UTC))

    report = load_source(rollback, store, SOURCE)

    assert len(report.artefacts) == 2
    assert report.artefacts[1].superseded == (report.artefacts[0].record_id,)
    outcomes = [row[1] for row in archived(rollback) if row[0] == "9004"]
    assert outcomes == ["unknown", "no_data"], "both stay; neither is rewritten"


def test_carried_sets_are_stored_for_the_archive_and_never_read_live(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    """D-325: history for the archive's denominator, invisible to every live read."""
    insert_satellite(rollback, NewSatellite(satellite_id="norad:57166", name="M2-3"))
    fetch(store, publish_public, PAGE, datetime(2026, 10, 9, 8, tzinfo=UTC))
    report = load_source(rollback, store, SOURCE)

    assert report.element_sets_written == 2
    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from element_sets"
            " where source = 'satnogs_network' and ingest_record_id is not null"
        )
        assert cur.fetchone()[0] == 2
    later = datetime(2026, 10, 8, tzinfo=UTC)
    assert find_element_set_current_at(rollback, "norad:57166", later) is None
    assert find_current_epochs(rollback, later) == {}
    assert find_current_element_sets(rollback, later) == {}


def test_a_carried_set_for_an_untracked_satellite_is_not_stored(
    rollback: Any, store: RawStore, publish_public: Any
) -> None:
    fetch(store, publish_public, PAGE, datetime(2026, 10, 9, 8, tzinfo=UTC))
    report = load_source(rollback, store, SOURCE)

    assert report.element_sets_written == 0
    assert report.element_sets_untracked == 2


def archive_passes_exported(conn: Any, root: Path) -> list[dict[str, Any]]:
    registry = PsycopgRegistry(
        conn, pepper="archive", recovery_window_s=3600, now_utc=lambda: EVENING
    )
    read = read_snapshot(conn, registry, since=datetime(2026, 10, 7, tzinfo=UTC))
    published = export_snapshot(read, root=root, created_at=EVENING)
    text = (published.path / "archive_passes.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_a_carried_set_gives_an_archive_station_its_passes(
    rollback: Any, store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    """D-150 with D-325: a day before our own fetches has a denominator.

    The positive control is the same archive without the sets it carried:
    every satellite-day then has no set current at its start, so no passes.
    """
    insert_satellite(rollback, NewSatellite(satellite_id="norad:57166", name="M2-3"))
    fetch(store, publish_public, PAGE, datetime(2026, 10, 9, 8, tzinfo=UTC))
    load_source(rollback, store, SOURCE)

    found = archive_passes_exported(rollback, tmp_path / "with")
    assert {row["archive_station_id"] for row in found} != set()
    assert all(row["satellite_id"] == "norad:57166" for row in found)

    with rollback.cursor() as cur:
        cur.execute("delete from element_sets where source = 'satnogs_network'")
    assert archive_passes_exported(rollback, tmp_path / "without") == []

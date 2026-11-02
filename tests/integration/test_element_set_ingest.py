"""Fetched element sets into ``element_sets``: tracked objects only, traced, once.

Stage 34's half of the load (D-320). A catalogue response is an artefact like
any other, and what it normalises to lands in its transaction. Four properties
are particular to element sets:

* only satellites the catalogue tracks are stored, and the rest are counted;
* each stored set names the artefact it was read from, and carries that
  artefact's retrieval instant rather than the load's;
* a set from a file cannot claim an artefact (migration 0032);
* the newest fetched set is the one pass generation is handed.

Reference: docs/DECISIONS.md D-049, D-057, D-139, D-320.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.catalogue_export import (  # noqa: E402 — after importorskip
    catalogue_document,
    read_held_catalogue,
)
from meridian.store.element_sets import (  # noqa: E402
    NewElementSet,
    find_current_epochs,
    find_element_set_current_at,
    insert_element_set,
)
from meridian.store.satellites import (  # noqa: E402
    NewSatellite,
    NewTransmitter,
    insert_satellite,
    insert_transmitter,
)
from meridian_ingest.adapters import REGISTRY  # noqa: E402
from meridian_ingest.adapters.protocol import FetchRequest  # noqa: E402
from meridian_ingest.load import load_source  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402

pytestmark = pytest.mark.integration

SOURCE = "celestrak_gp"
FIXTURES = Path(__file__).parents[1] / "unit" / "public_fixtures"
FETCHED = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

# Built from the same fields as the fixture's first object, a day older.
OLD_M2_3 = (
    "1 57166U 23091A   26305.50000000 -.00000020  00000+0  50000-5 0  9993",
    "2 57166  98.5900 357.5000 0004900 120.0000 240.1000 14.24055000174015",
)


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


@pytest.fixture
def tracked(rollback: Any) -> Any:
    """The catalogue an operator loaded: both Meteors, and no NOAA satellite."""
    for satellite_id, name in (("norad:57166", "Meteor-M2-3"), ("norad:59051", "M2-4")):
        insert_satellite(rollback, NewSatellite(satellite_id=satellite_id, name=name))
    return rollback


def fetch_weather(store: RawStore, publish_public: Any) -> Any:
    remote = REGISTRY[SOURCE].adapter.plan(FetchRequest(groups=("weather",)))[0]
    return publish_public(
        store, SOURCE, remote, FIXTURES / "celestrak-weather.tle", FETCHED
    )


def stored_sets(conn: Any) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "select e.satellite_id, e.source, e.retrieved_at, r.original_identifier"
            " from element_sets e join ingest_records r"
            " on r.record_id = e.ingest_record_id order by e.satellite_id"
        )
        return list(cur.fetchall())


def test_only_tracked_satellites_are_stored_and_the_rest_are_counted(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    fetch_weather(store, publish_public)
    report = load_source(tracked, store, SOURCE)

    assert report.element_sets_written == 2
    assert report.element_sets_untracked == 1, "NOAA 19 is not in the catalogue"
    with tracked.cursor() as cur:
        cur.execute(
            "select count(*) from satellites where satellite_id = 'norad:33591'"
        )
        assert cur.fetchone()[0] == 0, "ingest never adds a satellite"


def test_a_stored_set_names_its_artefact_and_when_that_was_fetched(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)

    assert stored_sets(tracked) == [
        ("norad:57166", "celestrak", FETCHED, "GROUP=weather"),
        ("norad:59051", "celestrak", FETCHED, "GROUP=weather"),
    ]


def test_a_second_load_writes_nothing(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)
    again = load_source(tracked, store, SOURCE)

    assert again.element_sets_written == 0
    assert again.element_sets_already_held == 2
    assert again.wrote_nothing()


def test_the_newest_fetched_set_is_the_one_pass_generation_is_handed(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    insert_element_set(
        tracked,
        NewElementSet(
            satellite_id="norad:57166",
            epoch=datetime(2026, 11, 1, 12, tzinfo=UTC),
            line1=OLD_M2_3[0],
            line2=OLD_M2_3[1],
            source="manual",
        ),
    )
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)

    current = find_element_set_current_at(
        tracked, "norad:57166", datetime(2026, 11, 2, 18, tzinfo=UTC)
    )
    assert current is not None
    assert current.source == "celestrak"
    assert current.epoch == datetime(2026, 11, 2, 12, tzinfo=UTC)


def test_a_set_from_a_file_cannot_claim_an_artefact(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)
    with tracked.cursor() as cur:
        cur.execute("select max(record_id) from ingest_records")
        record_id = cur.fetchone()[0]

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_element_set(
            tracked,
            NewElementSet(
                satellite_id="norad:57166",
                epoch=datetime(2026, 11, 1, 12, tzinfo=UTC),
                line1=OLD_M2_3[0],
                line2=OLD_M2_3[1],
                source="manual",
                ingest_record_id=record_id,
            ),
        )


def test_the_age_a_scrape_reports_is_the_set_pass_generation_uses(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    """D-321: the epoch current at the scrape, per satellite with a set."""
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)

    before = find_current_epochs(tracked, datetime(2026, 11, 2, 9, tzinfo=UTC))
    after = find_current_epochs(tracked, datetime(2026, 11, 2, 18, tzinfo=UTC))

    assert before == {"norad:59051": datetime(2026, 11, 2, 6, tzinfo=UTC)}
    assert after == {
        "norad:57166": datetime(2026, 11, 2, 12, tzinfo=UTC),
        "norad:59051": datetime(2026, 11, 2, 6, tzinfo=UTC),
    }


def test_an_export_carries_each_satellites_current_fetched_set(
    tracked: Any, store: RawStore, publish_public: Any
) -> None:
    insert_transmitter(
        tracked,
        NewTransmitter(
            satellite_id="norad:57166",
            centre_freq_hz=137_900_000,
            mode="lrpt",
            polarisation="rhcp",
            bandwidth_hz=150_000,
            frame_interval_s=0.113778,
        ),
    )
    fetch_weather(store, publish_public)
    load_source(tracked, store, SOURCE)

    document: Any = catalogue_document(
        *read_held_catalogue(tracked, datetime(2026, 11, 2, 18, tzinfo=UTC))
    )

    entries = {one["satellite_id"]: one for one in document["satellites"]}
    assert set(entries) == {"norad:57166", "norad:59051"}
    assert entries["norad:57166"]["transmitters"] == [
        {
            "centre_freq_hz": 137_900_000,
            "mode": "lrpt",
            "polarisation": "rhcp",
            "bandwidth_hz": 150_000,
            "frame_interval_s": 0.113778,
        }
    ]
    (exported,) = entries["norad:57166"]["element_sets"]
    assert exported["exported_from"]["source"] == "celestrak"
    assert exported["exported_from"]["retrieved_at"] == FETCHED.isoformat()

"""Stage 34's archive source, against a synthetic page in the network's answer shape.

What it plans and refuses to plan, how each rating maps to our words, and the
two ways a page could quietly mislead the archive — cut short, or about
another satellite — refused. No network (D-142).

Reference: docs/DECISIONS.md D-139, D-142, D-322, D-323.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.adapters.public.satnogs_network import (
    PAGE_SIZE,
    SATNOGS_NETWORK,
    archive_outcome,
)
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.raw_store import RawStore, StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact

FIXTURES = Path(__file__).parent / "public_fixtures"
PAGE = FIXTURES / "satnogs-network-57166.json"
SOURCE = SATNOGS_NETWORK.source_id
FETCHED = datetime(2026, 10, 9, 8, tzinfo=UTC)
NOON = datetime(2026, 10, 7, 12, tzinfo=UTC)
EVENING = datetime(2026, 10, 7, 18, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


def plan(**request: object) -> tuple[RemoteArtefact, ...]:
    return REGISTRY[SOURCE].adapter.plan(FetchRequest(**request))  # type: ignore[arg-type]


def noon_window() -> RemoteArtefact:
    (remote,) = plan(catalog_numbers=(57166,), since=NOON, until=EVENING)
    return remote


def fetched(store: RawStore, publish_public: Any, file: Path) -> StoredArtefact:
    return publish_public(store, SOURCE, noon_window(), file, FETCHED)


def normalise(artefact: StoredArtefact) -> NormalisedBatch:
    return REGISTRY[SOURCE].normaliser.normalise(artefact)


# --- the source --------------------------------------------------------------


def test_it_is_registered_off_and_credited_before_any_retrieval() -> None:
    registration = REGISTRY[SOURCE]
    assert not registration.enabled_by_default
    assert registration.adapter.descriptor.source_class == "archive_receptions"
    assert "CC BY-SA 4.0" in SATNOGS_NETWORK.licence
    attribution = (Path(__file__).parents[2] / "ATTRIBUTION.md").read_text("utf-8")
    assert SATNOGS_NETWORK.attribution_entry in attribution


def test_it_publishes_no_limit_so_none_is_declared() -> None:
    """D-223: only a source's own published limits are declared."""
    assert REGISTRY[SOURCE].rate_limits == ()


# --- planning ----------------------------------------------------------------


def test_one_request_is_one_satellite_over_one_whole_six_hour_window() -> None:
    planned = plan(
        catalog_numbers=(57166, 59051),
        since=datetime(2026, 10, 7, 1, tzinfo=UTC),
        until=datetime(2026, 10, 7, 19, tzinfo=UTC),
    )
    assert [one.original_identifier for one in planned] == [
        "norad=57166/2026-10-07T06:00:00Z",
        "norad=57166/2026-10-07T12:00:00Z",
        "norad=59051/2026-10-07T06:00:00Z",
        "norad=59051/2026-10-07T12:00:00Z",
    ]
    assert planned[0].url == (
        "https://network.satnogs.org/api/observations/?format=json"
        "&norad_cat_id=57166&start=2026-10-07T06:00:00Z&end=2026-10-07T12:00:00Z"
    )
    assert (planned[0].valid_from, planned[0].valid_to) == (
        datetime(2026, 10, 7, 6, tzinfo=UTC),
        datetime(2026, 10, 7, 12, tzinfo=UTC),
    )


def test_a_window_still_open_is_left_for_a_later_fetch() -> None:
    planned = plan(
        catalog_numbers=(57166,),
        since=NOON,
        until=datetime(2026, 10, 7, 17, tzinfo=UTC),
    )
    assert planned == ()


def test_a_fetch_that_names_no_satellite_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="catalog_numbers"):
        plan(since=NOON, until=EVENING)


def test_a_fetch_with_no_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="--since and --until"):
        plan(catalog_numbers=(57166,))


def test_more_windows_than_the_budget_is_refused_not_cut_short() -> None:
    with pytest.raises(ValueError, match="limit is 3"):
        plan(
            catalog_numbers=(57166,),
            since=NOON,
            until=datetime(2026, 10, 9, tzinfo=UTC),
            limit=3,
        )


# --- normalising -------------------------------------------------------------


def test_a_page_is_the_receptions_that_started_in_its_window(
    store: RawStore, publish_public: Any
) -> None:
    batch = normalise(fetched(store, publish_public, PAGE))

    assert batch.transformation_version == "satnogs-network-1"
    by_id = {one.source_observation_id: one for one in batch.receptions}
    assert sorted(by_id) == ["9001", "9002", "9003", "9004", "9005"]
    assert "9006" not in by_id, "a future observation has not happened"
    assert "9007" not in by_id, "it starts after the window, so the next one has it"
    first = by_id["9001"]
    assert first.satellite_key == "norad:57166"
    assert first.satellite_key_kind == "norad"
    assert first.started_at == datetime(2026, 10, 7, 12, 10, tzinfo=UTC)
    assert first.max_elevation_deg == 72.0
    assert first.centre_freq_hz == 137_900_000
    assert first.mode == "lrpt"


def test_each_rating_is_ours_and_theirs_is_kept_verbatim(
    store: RawStore, publish_public: Any
) -> None:
    batch = normalise(fetched(store, publish_public, PAGE))
    outcomes = {
        one.source_observation_id: (one.archive_outcome, one.source_outcome)
        for one in batch.receptions
    }
    assert outcomes == {
        "9001": ("decoded", "status=good;waterfall_status=with-signal"),
        "9002": ("signal_no_decode", "status=good;waterfall_status=with-signal"),
        "9003": ("no_data", "status=bad;waterfall_status=without-signal"),
        "9004": ("unknown", "status=failed;waterfall_status=unknown"),
        "9005": ("signal_no_decode", "status=unknown;waterfall_status=with-signal"),
    }


def test_no_rating_is_ever_no_signal() -> None:
    """D-139: we hold no heartbeat for another network's station."""
    for status in ("good", "bad", "unknown", "failed", "anything"):
        for waterfall in ("with-signal", "without-signal", "unknown"):
            for frames in (0, 1):
                assert archive_outcome(status, waterfall, frames) != "no_signal"


def test_each_station_is_described_once_with_its_published_place(
    store: RawStore, publish_public: Any
) -> None:
    batch = normalise(fetched(store, publish_public, PAGE))
    stations = {one.source_station_key: one for one in batch.stations}
    assert sorted(stations) == ["101", "102"]
    assert (stations["101"].lat_deg, stations["101"].lon_deg) == (12.5, 77.25)


def test_a_full_page_is_refused_because_it_may_be_cut_short(
    store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    one = json.loads(PAGE.read_text("utf-8"))[-1]
    full = tmp_path / "full.json"
    full.write_text(
        json.dumps([{**one, "id": 1000 + n} for n in range(PAGE_SIZE)]), "utf-8"
    )
    with pytest.raises(NormalisationError, match="full page"):
        normalise(fetched(store, publish_public, full))


def test_a_page_about_another_satellite_is_refused(
    store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    """The filter ignored is the page the archive must not believe."""
    page = json.loads(PAGE.read_text("utf-8"))
    page[0]["norad_cat_id"] = 67683
    other = tmp_path / "other.json"
    other.write_text(json.dumps(page), "utf-8")
    with pytest.raises(NormalisationError, match="filter was not applied"):
        normalise(fetched(store, publish_public, other))


def test_normalising_twice_gives_the_same_rows(
    store: RawStore, publish_public: Any
) -> None:
    artefact = fetched(store, publish_public, PAGE)
    first, again = normalise(artefact), normalise(artefact)
    assert [one.content_sha256 for one in first.receptions] == [
        one.content_sha256 for one in again.receptions
    ]


# --- the element sets a page carries -----------------------------------------


def test_each_distinct_set_a_kept_observation_carries_is_one_element_set(
    store: RawStore, publish_public: Any
) -> None:
    """D-325: the archive's own history, read once per distinct set."""
    batch = normalise(fetched(store, publish_public, PAGE))

    epochs = sorted(one.epoch for one in batch.element_sets)
    assert epochs == [
        datetime(2026, 10, 6, 6, tzinfo=UTC),
        datetime(2026, 10, 7, 6, tzinfo=UTC),
    ]
    assert {one.satellite_key for one in batch.element_sets} == {"norad:57166"}
    assert {one.name for one in batch.element_sets} == {"0 METEOR-M2 3"}


def test_a_set_carried_for_another_satellite_refuses_the_page(
    store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    page = json.loads(PAGE.read_text("utf-8"))
    page[-1]["tle1"] = page[-1]["tle1"].replace("57166", "59051", 1)
    other = tmp_path / "wrong-set.json"
    other.write_text(json.dumps(page), "utf-8")
    with pytest.raises(NormalisationError, match=r"\[6\]"):
        normalise(fetched(store, publish_public, other))


def test_an_observation_without_a_set_carries_none(
    store: RawStore, publish_public: Any, tmp_path: Path
) -> None:
    page = json.loads(PAGE.read_text("utf-8"))
    for one in page:
        one["tle1"] = one["tle2"] = ""
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(page), "utf-8")
    assert normalise(fetched(store, publish_public, bare)).element_sets == ()

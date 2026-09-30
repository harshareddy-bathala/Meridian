"""Stage 31's completion gate, asserted with the network refused.

    Every feature the prediction module can read from a public source is
    reproducible from a snapshot on a machine with no network access, with its
    provenance, and the value for any pass is the one published before that
    pass.

The chain is the real one after the socket: synthetic artefacts in the
providers' formats are published into a raw store as a fetch would, the
fixtures are deleted, and everything else — normalising, freezing into a raw
snapshot as the export writes one, reading the features — runs under a guard
that refuses every socket and database connection. It runs three times and the
features must be identical.

The rule is checked where it can fail: the Kp product is fetched twice, a day
apart, and the second fetch revises an interval. A pass between the two
fetches must read the first value, and a pass after the second the revision.

Reference: docs/DECISIONS.md D-131, D-142, D-143, D-221, D-222, D-224.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.environment_rows import read_environment_samples
from meridian.datasets.publish import read_directory
from meridian.prediction.conditions import CLOUD, KP, Rule, value_before
from meridian.prediction.feature_rows import read_feature_rows
from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.extent import GeoPoint
from meridian_ingest.raw_store import RawStore

FIRST_FETCH = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECOND_FETCH = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
STATION = ("st_a", 12.9716, 77.5946)
REVISED = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
"""The interval the second fetch revises, from Kp 1.0 to 1.67."""


def raw_store(tmp_path: Path, publish_public: Any) -> RawStore:
    """Fixtures copied, published as fetched, and the copies deleted."""
    source = Path(__file__).parent / "public_fixtures"
    copies = tmp_path / "fixtures"
    shutil.copytree(source, copies)
    store = RawStore(tmp_path / "raw")
    kp = REGISTRY["noaa_swpc_kp"].adapter.plan(FetchRequest())[0]
    publish_public(
        store, "noaa_swpc_kp", kp, copies / "swpc-kp-2026-09-28.json", FIRST_FETCH
    )
    publish_public(
        store, "noaa_swpc_kp", kp, copies / "swpc-kp-2026-09-29.json", SECOND_FETCH
    )
    cloud = REGISTRY["open_meteo_cloud"].adapter.plan(
        FetchRequest(points=(GeoPoint(STATION[1], STATION[2]),))
    )[0]
    publish_public(
        store, "open_meteo_cloud", cloud, copies / "open-meteo-cloud.json", FIRST_FETCH
    )
    shutil.rmtree(copies)
    return store


def snapshot_rows(store: RawStore) -> dict[str, list[dict[str, object]]]:
    """What an export would freeze, built from the raw store alone."""
    records: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    for source_id in ("noaa_swpc_kp", "open_meteo_cloud"):
        for raw_path in store.scan(source_id):
            artefact = store.read(raw_path)
            record_id = len(records) + 1
            records.append(
                {
                    "record_id": record_id,
                    "source_id": source_id,
                    "raw_path": raw_path,
                    "sha256": artefact.manifest.sha256,
                    "retrieved_at": artefact.manifest.provenance.retrieved_at,
                }
            )
            batch = REGISTRY[source_id].normaliser.normalise(artefact)
            for one in batch.samples:
                samples.append(
                    {
                        "sample_id": len(samples) + 1,
                        "record_id": record_id,
                        "source_id": source_id,
                        "transformation_version": batch.transformation_version,
                        "series_key": one.series_key,
                        "content_sha256": one.content_sha256,
                        "quantity": one.quantity,
                        "value": one.value,
                        "missing_reason": one.missing_reason,
                        "value_unit": one.value_unit,
                        "observed_from": one.observed_from,
                        "observed_to": one.observed_to,
                        "published_at": one.published_at,
                        "published_basis": one.published_basis,
                        "product": one.product,
                        "lat_deg": one.lat_deg,
                        "lon_deg": one.lon_deg,
                        "footprint_m": one.footprint_m,
                        "quality": one.quality,
                    }
                )
    return {
        "ingest_records": records,
        "environment_samples": samples,
        "stations": [
            {"station_id": STATION[0], "lat_deg": STATION[1], "lon_deg": STATION[2]}
        ],
    }


def features(tmp_path: Path, publish_public: Any, raw_snapshot: Any) -> list[object]:
    """The chain after the socket, returning what a pass at each instant reads."""
    store = raw_store(tmp_path, publish_public)
    path = raw_snapshot(snapshot_rows(store))
    files = read_directory(path).files
    rows = read_feature_rows(files)
    return [
        rows.conditions.values(STATION[0], at)
        for at in (
            datetime(2026, 9, 28, 1, 30, tzinfo=UTC),
            FIRST_FETCH + timedelta(hours=1),
            SECOND_FETCH + timedelta(hours=1),
        )
    ]


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


def test_features_are_rebuilt_three_times_identically_with_nothing_reachable(
    tmp_path: Path, publish_public: Any, raw_snapshot: Any, guarded: Any
) -> None:
    runs = [features(tmp_path / str(n), publish_public, raw_snapshot) for n in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert guarded.attempts == []


def test_a_pass_reads_only_what_was_published_before_it(
    tmp_path: Path, publish_public: Any, raw_snapshot: Any
) -> None:
    before_any, after_first, after_second = features(
        tmp_path, publish_public, raw_snapshot
    )
    assert before_any == (0.0, 0.0, 0.0, 0.0)
    assert after_first[:2] == (1.0, 1.0)
    assert after_first[3] == 1.0
    assert after_second[:2] == (1.67, 1.0)
    assert after_second[2:] == (0.0, 0.0)


def test_a_revision_published_later_never_reaches_an_earlier_pass(
    tmp_path: Path, publish_public: Any, raw_snapshot: Any
) -> None:
    """The snapshot holds both values of the revised interval; each pass reads one."""
    store = raw_store(tmp_path, publish_public)
    samples = read_environment_samples(
        read_directory(raw_snapshot(snapshot_rows(store))).files
    )
    interval = [
        one
        for one in samples
        if one.quantity == "kp_index" and one.observed_from == REVISED
    ]
    assert sorted(one.value for one in interval) == [1.0, 1.67]

    patient = Rule(
        "kp_index", covering=False, max_age=timedelta(days=2), within_km=None
    )
    earlier = value_before(interval, patient, REVISED + timedelta(hours=2), None)
    later = value_before(interval, patient, SECOND_FETCH + timedelta(minutes=1), None)
    assert earlier.value == 1.0
    assert later.value == 1.67
    assert earlier.sample is not None and later.sample is not None
    assert earlier.sample.published_at == FIRST_FETCH
    assert later.sample.published_at == SECOND_FETCH


def test_every_value_a_pass_reads_names_the_artefact_it_came_from(
    tmp_path: Path, publish_public: Any, raw_snapshot: Any
) -> None:
    store = raw_store(tmp_path, publish_public)
    files = read_directory(raw_snapshot(snapshot_rows(store))).files
    samples = read_environment_samples(files)
    records = files["ingest_records.jsonl"].decode()
    at = FIRST_FETCH + timedelta(hours=1)
    for rule, place in ((KP, None), (CLOUD, STATION[1:])):
        choice = value_before(samples, rule, at, place)
        assert choice.sample is not None
        assert f'"record_id":{choice.sample.record_id},' in records

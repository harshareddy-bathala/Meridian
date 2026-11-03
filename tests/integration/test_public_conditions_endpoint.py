"""``/api/v1/conditions`` and ``/api/v1/insights/model``, end to end.

With nothing ingested, Kp is stated as not measured and no cloud is listed.
Otherwise the newest Kp published before now is served with its source's credit;
each point's cloud cover is the forecast covering now, its point to one decimal,
and a published gap is served as the gap. The model insight is not yet computed.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-082, D-086, D-134, D-221, D-222, D-331.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api import platform_clock  # noqa: E402
from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.api.public.models.conditions import NO_KP_REASON  # noqa: E402
from meridian.store.environment_samples import (  # noqa: E402
    NewEnvironmentSample,
    insert_environment_sample,
)
from meridian.store.ingest_records import (  # noqa: E402
    NewIngestRecord,
    insert_ingest_record,
)
from meridian.store.ingest_sources import (  # noqa: E402
    NewIngestSource,
    insert_ingest_source,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
KP = "noaa_swpc_kp"
CLOUD = "open_meteo_cloud"
_DIGESTS = itertools.count(1)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(platform_clock, "utc_now", lambda: NOW)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def source(conn: Any, source_id: str, source_class: str, name: str) -> None:
    insert_ingest_source(
        conn,
        NewIngestSource(
            source_id=source_id,
            source_class=source_class,
            name=name,
            licence="US Government work",
            terms_url=f"https://example.invalid/{source_id}",
            access_constraint="none",
            attribution_entry=name,
        ),
    )


def sample(  # noqa: PLR0913 — one row's parts, each by name
    conn: Any,
    source_id: str,
    quantity: str,
    observed: datetime,
    hours: float,
    *,
    published: datetime | None = None,
    value: float | None = 2.33,
    point: tuple[float, float] | None = None,
) -> None:
    digest = next(_DIGESTS)
    published_at = published or observed
    record = insert_ingest_record(
        conn,
        NewIngestRecord(
            source_id=source_id,
            original_identifier=f"art-{digest}",
            source_version="served now",
            payload_kind="data",
            retrieved_at=published_at,
            sha256=digest.to_bytes(32, "big"),
            raw_path=f"{source_id}/20261102T000000Z-{digest:04d}",
            media_type="application/json",
            byte_count=64,
        ),
    ).record_id
    insert_environment_sample(
        conn,
        NewEnvironmentSample(
            record_id=record,
            source_id=source_id,
            transformation_version="test-1",
            series_key=f"{quantity}:{digest}",
            content_sha256=digest.to_bytes(32, "big"),
            quantity=quantity,
            value_unit="Kp (0-9)" if quantity == "kp_index" else "%",
            observed_from=observed,
            observed_to=observed + timedelta(hours=hours),
            published_at=published_at,
            published_basis="retrieved",
            product="test product",
            value=value,
            missing_reason=None if value is not None else "source published a gap",
            lat_deg=None if point is None else point[0],
            lon_deg=None if point is None else point[1],
        ),
    )


def test_nothing_ingested_states_kp_as_not_measured(client: TestClient) -> None:
    body = client.get("/api/v1/conditions").json()

    assert body["status"] == "computed"
    assert body["kp"] == {"status": "not_measured", "reason": NO_KP_REASON}
    assert body["cloud_cover"] == []


def test_the_newest_kp_published_before_now_is_served_with_its_credit(
    client: TestClient, rollback: Any
) -> None:
    source(rollback, "noaa_swpc_kp", "space_weather", "NOAA SWPC Kp")
    sample(rollback, KP, "kp_index", NOW - timedelta(hours=6), 3, value=1.0)
    sample(rollback, KP, "kp_index", NOW - timedelta(hours=3), 3, value=3.67)
    sample(
        rollback,
        KP,
        "kp_index",
        NOW - timedelta(hours=1),
        3,
        published=NOW + timedelta(hours=1),
        value=8.0,
    )

    kp = client.get("/api/v1/conditions").json()["kp"]

    assert kp["value"] == 3.67
    assert kp["quantity"] == "kp_index"
    assert kp["lat_deg"] is None
    assert kp["source"] == {
        "source_id": "noaa_swpc_kp",
        "name": "NOAA SWPC Kp",
        "licence": "US Government work",
        "terms_url": "https://example.invalid/noaa_swpc_kp",
    }


def test_each_points_cloud_is_the_forecast_covering_now_to_one_decimal(
    client: TestClient, rollback: Any
) -> None:
    source(rollback, "open_meteo_cloud", "atmospheric", "Open-Meteo cloud cover")
    hour = NOW.replace(minute=0, second=0, microsecond=0)
    here, there = (12.97, 77.59), (13.04, 77.52)
    sample(rollback, CLOUD, "cloud_cover", hour - timedelta(hours=1), 1)
    sample(rollback, CLOUD, "cloud_cover", hour, 1, value=64.0, point=here)
    sample(
        rollback,
        CLOUD,
        "cloud_cover",
        hour - timedelta(hours=2),
        1,
        value=10.0,
        point=there,
    )
    sample(rollback, CLOUD, "cloud_cover", hour, 1, value=None, point=there)

    cloud = client.get("/api/v1/conditions").json()["cloud_cover"]

    assert [(one["lat_deg"], one["lon_deg"]) for one in cloud] == [
        (13.0, 77.6),
        (13.0, 77.5),
    ]
    assert cloud[0]["value"] == 64.0
    assert cloud[0]["missing_reason"] is None
    assert cloud[1]["value"] is None
    assert cloud[1]["missing_reason"] == "source published a gap"


def test_the_model_insight_is_not_yet_computed(client: TestClient) -> None:
    body = client.get("/api/v1/insights/model").json()

    assert body["status"] == "not_yet_computed"
    assert body["available_from_stage"] == 34
    assert set(body) == {"status", "reason", "available_from_stage"}


SUMMARY = {
    "schema": "model-summary-1",
    "run_sha256": "ab" * 32,
    "method": "report-9",
    "label": "Measured by the SatNOGS network, not by Meridian. Selection-biased.",
    "source": {
        "name": "SatNOGS Network",
        "licence": "CC BY-SA 4.0",
        "terms_url": "https://network.satnogs.org/terms",
        "retrieved_from": "2026-10-01T00:00:00+00:00",
        "retrieved_until": "2026-10-29T00:00:00Z",
    },
    "simulated": False,
    "completeness_ratio": None,
    "split": {
        "train_until": "2026-10-15T00:00:00.000Z",
        "validate_until": "2026-10-22T00:00:00.000Z",
        "as_of": "2026-10-29T00:00:00Z",
    },
    "configurations": [
        {"name": "C", "computable": False, "reason": "needs a station profile"}
    ],
}
"""A valid summary; ``tests/unit/test_model_summary.py`` checks the format itself."""


@pytest.fixture
def named(
    rollback: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> Iterator[tuple[TestClient, Any]]:
    """An app whose ``REPORT_SUMMARY`` names a file in ``tmp_path``."""
    path = tmp_path / "model-summary.json"
    monkeypatch.setenv("REPORT_SUMMARY", str(path))
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started, path


def test_a_named_summary_is_served_with_its_label(
    named: tuple[TestClient, Any],
) -> None:
    client, path = named
    path.write_text(json.dumps(SUMMARY))

    body = client.get("/api/v1/insights/model").json()

    assert body["status"] == "computed"
    assert body["label"].startswith("Measured by the SatNOGS network")
    assert body["simulated"] is False
    assert body["configurations"] == [
        {"name": "C", "computable": False, "reason": "needs a station profile"}
    ]


def test_a_named_summary_that_is_missing_or_damaged_is_refused(
    named: tuple[TestClient, Any],
) -> None:
    client, path = named
    missing = client.get("/api/v1/insights/model")
    path.write_text('{"schema": "model-summary-2"}')
    damaged = client.get("/api/v1/insights/model")

    for response in (missing, damaged):
        assert response.status_code == 500
        assert response.json()["error"] == "server_error"
        assert "REPORT_SUMMARY" in response.json()["message"]

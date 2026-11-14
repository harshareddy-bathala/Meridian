"""``/api/v1/areas``: closed until a deployment publishes, then exactly the file.

D-326, accepted on 2026-11-07 (D-366), makes a registration public, and
``AREAS_PUBLISHED`` is how a deployment chooses to publish its areas. Closed,
the body is D-086's placeholder. Open, it is the summary
``meridian regions summary`` wrote, parsed strictly: a file with a key it does
not know, or another schema, is a ``server_error``, never part of a page.

No database: the endpoint reads a file. Marked ``msp_conformance`` by the
directory hook.

Reference: docs/DECISIONS.md D-086, D-326, D-366.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app

SUMMARY = {
    "schema": "areas-summary-1",
    "report_sha256": "a" * 64,
    "method": "regions-1",
    "since": "2026-10-05T00:00:00+00:00",
    "as_of": "2026-11-02T00:00:00+00:00",
    "sources": [
        {
            "source_id": "noaa_swpc_kp",
            "licence": "US Government work",
            "terms_url": "https://www.swpc.noaa.gov/disclaimer",
        }
    ],
    "areas": [
        {
            "area_id": 1,
            "label": "Bengaluru urban",
            "area_km2": 741.0,
            "bbox": [77.46, 12.83, 77.75, 13.14],
        }
    ],
    "series": [
        {
            "area_id": 1,
            "quantity": "ndvi",
            "unit": "1",
            "period_from": "2026-10-05T00:00:00+00:00",
            "period_until": "2026-10-21T00:00:00+00:00",
            "value": 0.41,
            "missing_reason": None,
            "count": 3,
            "sources": ["ornl_modis_ndvi"],
        }
    ],
    "changes": [
        {
            "area_id": 1,
            "quantity": "ndvi",
            "verdict": "no_change",
            "reason": "the interval holds zero",
            "baseline": {
                "from": "2026-10-05T00:00:00+00:00",
                "until": "2026-10-21T00:00:00+00:00",
            },
            "current": {
                "from": "2026-10-21T00:00:00+00:00",
                "until": "2026-11-02T00:00:00+00:00",
            },
            "baseline_mean": 0.41,
            "current_mean": 0.4,
            "change": -0.01,
            "low": -0.05,
            "high": 0.03,
        }
    ],
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    monkeypatch.delenv("AREAS_PUBLISHED", raising=False)
    monkeypatch.delenv("AREAS_SUMMARY", raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as started:
        yield started


def published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: object) -> None:
    path = tmp_path / "areas.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("AREAS_PUBLISHED", "true")
    monkeypatch.setenv("AREAS_SUMMARY", str(path))


def test_closed_by_default_with_no_field_a_chart_could_read(client: TestClient) -> None:
    body = client.get("/api/v1/areas").json()
    assert set(body) == {"status", "reason", "available_from_stage"}
    assert body["status"] == "not_yet_computed"
    assert "D-326" in body["reason"]


def test_a_file_without_the_flag_stays_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Writing the file is not the team's acceptance; the flag is (D-326)."""
    published(monkeypatch, tmp_path, SUMMARY)
    monkeypatch.delenv("AREAS_PUBLISHED")
    assert client.get("/api/v1/areas").json()["status"] == "not_yet_computed"


def test_open_it_is_the_file_exactly_and_never_simulated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    published(monkeypatch, tmp_path, SUMMARY)
    response = client.get("/api/v1/areas")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "computed"
    assert body["simulated"] is False
    assert body["summary"] == SUMMARY


@pytest.mark.parametrize(
    "spoiled",
    [
        {**SUMMARY, "schema": "areas-summary-2"},
        {**SUMMARY, "notes": "a note leaks"},
        {**SUMMARY, "areas": [{**SUMMARY["areas"][0], "owner": "someone"}]},  # type: ignore[dict-item]
    ],
)
def test_a_file_it_does_not_recognise_is_a_server_error_not_a_partial_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spoiled: object
) -> None:
    published(monkeypatch, tmp_path, spoiled)
    response = client.get("/api/v1/areas")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "server_error"
    assert "AREAS_SUMMARY" in body["message"]

"""``/api/v1/stations/{id}/receive-chain``, and the scrape's count, end to end.

A chain not yet watched is published with a null baseline; a watched one with
its baseline and its warnings, newest first, decibels to a tenth; only the
deployment's own baselines and warnings are read; and the scrape's count is
per population, absent for a population with no baseline.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-291, D-292, D-295.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.reliability.watch_run import watched_under  # noqa: E402
from meridian.store.receive_chain_warnings import (  # noqa: E402
    NewWarning,
    clear_warning,
    raise_warning,
)
from meridian.store.signal_baselines import NewBaseline, insert_baseline  # noqa: E402
from meridian.store.watch_reads import WatchCount, count_watched  # noqa: E402

pytestmark = pytest.mark.integration

STATION = "st_chain_api"
UNDER = watched_under(ReliabilityConfig())
RAISED = datetime(2026, 10, 22, 6, 5, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def capability(conn: Any, station: str, low: int) -> int:
    return int(
        conn.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', %s, %s, '{lrpt}', 'rhcp', 5) returning id",
            (station, low, low + 1_000_000),
        ).fetchone()[0]
    )


def baseline(conn: Any, station: str, chain: int, **changes: Any) -> int:
    values: dict[str, Any] = {
        "station_id": station,
        "capability_id": chain,
        "evidence": "samples",
        "bin_width_deg": 10.0,
        "bins": [
            {
                "elevation_deg": 10.0,
                "median_db": 4.234,
                "p10_db": 2.06,
                "receptions": 9,
                "samples": 120,
            },
            {
                "elevation_deg": 20.0,
                "median_db": 7.41,
                "p10_db": 5.0,
                "receptions": 8,
                "samples": 90,
            },
        ],
        "receptions": 12,
        "trained_from": datetime(2026, 10, 6, tzinfo=UTC),
        "trained_until": datetime(2026, 10, 20, tzinfo=UTC),
        "method": UNDER.baseline_method,
        "config_sha256": UNDER.baseline_sha256,
    } | changes
    return insert_baseline(conn, NewBaseline(**values))


def warning(conn: Any, base: int, at: datetime, **changes: Any) -> int | None:
    values: dict[str, Any] = {
        "baseline_id": base,
        "raised_at": at,
        "shortfall_db": 4.24,
        "affected_bins": [
            {"elevation_deg": 10.0, "shortfall_db": 4.44, "receptions": 3}
        ],
        "evidence": {},
        "method": UNDER.watch_method,
        "config_sha256": UNDER.watch_sha256,
    } | changes
    return raise_warning(conn, NewWarning(**values))


def test_a_chain_not_yet_watched_has_a_null_baseline(
    client: TestClient, rollback: Any, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    capability(rollback, STATION, 137_000_000)

    body = client.get(f"/api/v1/stations/{STATION}/receive-chain").json()

    assert body["station_id"] == STATION
    assert body["simulated"] is True
    assert body["chains"] == [
        {
            "band": "vhf",
            "freq_min_hz": 137_000_000,
            "freq_max_hz": 138_000_000,
            "baseline": None,
            "warnings": [],
        }
    ]


def test_a_watched_chain_has_its_baseline_and_warnings_newest_first(
    client: TestClient, rollback: Any, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=False)
    chain = capability(rollback, STATION, 137_000_000)
    base = baseline(rollback, STATION, chain)
    first = warning(rollback, base, RAISED)
    assert first is not None
    clear_warning(rollback, first, at=RAISED + timedelta(days=1), shortfall_db=0.62)
    warning(rollback, base, RAISED + timedelta(days=3))
    # Another configuration's rows are not this deployment's.
    other = baseline(rollback, STATION, chain, config_sha256=bytes(32))
    warning(rollback, other, RAISED + timedelta(days=4), config_sha256=bytes(32))

    (published,) = client.get(f"/api/v1/stations/{STATION}/receive-chain").json()[
        "chains"
    ]

    assert published["baseline"]["evidence"] == "samples"
    assert published["baseline"]["bins"][0] == {
        "elevation_deg": 10.0,
        "median_db": 4.2,
        "p10_db": 2.1,
        "receptions": 9,
    }
    newest, older = published["warnings"]
    assert newest["cleared_at"] is None
    assert newest["shortfall_db"] == 4.2
    assert newest["affected_bins"] == [
        {"elevation_deg": 10.0, "shortfall_db": 4.4, "receptions": 3}
    ]
    assert older["cleared_shortfall_db"] == 0.6
    assert "capability_id" not in published


def test_an_unknown_station_is_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/stations/st_nobody/receive-chain")

    assert response.status_code == 404


def test_the_scrape_counts_chains_and_open_warnings_per_population(
    rollback: Any, schedule_rows: Any
) -> None:
    before = {one.simulated: one for one in count_watched(rollback, under=UNDER)}
    schedule_rows.station("st_chain_sim", simulated=True)
    schedule_rows.station("st_chain_real", simulated=False)
    simulated = baseline(
        rollback, "st_chain_sim", capability(rollback, "st_chain_sim", 137_000_000)
    )
    baseline(
        rollback, "st_chain_real", capability(rollback, "st_chain_real", 137_000_000)
    )
    warning(rollback, simulated, RAISED)

    after = {one.simulated: one for one in count_watched(rollback, under=UNDER)}

    def grew(simulated: bool) -> tuple[int, int]:
        was = before.get(simulated, WatchCount(simulated, 0, 0))
        now = after[simulated]
        return now.chains - was.chains, now.open - was.open

    assert grew(True) == (1, 1)
    assert grew(False) == (1, 0)

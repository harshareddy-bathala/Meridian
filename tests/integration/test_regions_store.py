"""Areas and regional alerts in TimescaleDB, and what a snapshot carries of them.

Reference: docs/DECISIONS.md D-227, D-229, D-232.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.regions.alerts import RecordOnlyDelivery, record_alerts  # noqa: E402
from meridian.regions.areas import new_area  # noqa: E402
from meridian.regions.geometry import Polygon  # noqa: E402
from meridian.store.areas_of_interest import (  # noqa: E402
    NewAreaRow,
    insert_area,
    list_areas,
    retire_area,
)
from meridian.store.region_alerts import (  # noqa: E402
    NewRegionAlert,
    find_deliveries,
)
from meridian.store.snapshot_reads import (  # noqa: E402
    SNAPSHOT_TABLES,
    SnapshotScope,
    read_table,
)

pytestmark = pytest.mark.integration

SHAPE = Polygon.from_bbox(77.45, 12.85, 77.75, 13.10)
T = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def area_row(polygon: Polygon = SHAPE, notes: str | None = "watershed") -> NewAreaRow:
    area = new_area("Bengaluru urban", polygon, notes)
    lat, lon = area.centroid
    return NewAreaRow(
        label=area.label,
        geometry=polygon.to_geojson(),
        geometry_sha256=polygon.sha256,
        centroid_lat_deg=lat,
        centroid_lon_deg=lon,
        area_km2=area.area_km2,
        notes=area.notes,
    )


def an_alert(area_id: int, alert_id: str = "ra_" + "a" * 24) -> NewRegionAlert:
    return NewRegionAlert(
        alert_id=alert_id,
        area_id=area_id,
        quantity="ndvi",
        rule="relative",
        baseline_from=T - timedelta(days=90),
        baseline_until=T - timedelta(days=30),
        current_from=T,
        current_until=T + timedelta(days=28),
        baseline_value=0.61,
        current_value=0.41,
        change=-0.33,
        change_low=-0.35,
        change_high=-0.31,
        threshold=-0.15,
        report_sha256=bytes([3]) * 32,
        summary="area 1: ndvi changed -0.330",
    )


def test_an_area_is_registered_once_per_shape(rollback: Any) -> None:
    first = insert_area(rollback, area_row())
    again = insert_area(rollback, area_row())
    assert first.written and not again.written
    assert again.area_id == first.area_id


def test_retiring_keeps_the_area(rollback: Any) -> None:
    area_id = insert_area(rollback, area_row()).area_id
    assert retire_area(rollback, area_id)
    assert not retire_area(rollback, area_id)
    held = [one for one in list_areas(rollback) if one.area_id == area_id]
    assert held and not held[0].active


def test_a_geometry_that_is_not_a_polygon_is_refused_by_the_database(
    rollback: Any,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        rollback.execute(
            "insert into areas_of_interest (label, geometry, geometry_sha256,"
            " centroid_lat_deg, centroid_lon_deg, area_km2)"
            " values ('x', '{\"type\": \"Point\"}', %s, 0, 0, 1)",
            (bytes(32),),
        )


def test_an_alert_is_recorded_once_and_delivered_once(rollback: Any) -> None:
    area_id = insert_area(rollback, area_row()).area_id
    alert = an_alert(area_id)
    first = record_alerts(rollback, [alert], RecordOnlyDelivery())
    again = record_alerts(rollback, [alert], RecordOnlyDelivery())
    assert (first.written, again.written) == (1, 0)
    deliveries = find_deliveries(rollback, alert.alert_id)
    assert [(one.channel, one.outcome) for one in deliveries] == [
        ("record_only", "recorded")
    ]
    assert "Stage 29" in deliveries[0].detail


def test_an_alert_whose_interval_excludes_its_change_is_refused(rollback: Any) -> None:
    area_id = insert_area(rollback, area_row()).area_id
    bad = an_alert(area_id)
    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        rollback.execute(
            "insert into region_alerts (alert_id, area_id, quantity, rule,"
            " baseline_from, baseline_until, current_from, current_until,"
            " baseline_value, current_value, change, change_low, change_high,"
            " threshold, report_sha256, summary) values"
            " (%s, %s, 'ndvi', 'relative', %s, %s, %s, %s, 1, 1, 0.5, -0.1, 0.1,"
            " 0.2, %s, 's')",
            (
                bad.alert_id,
                area_id,
                bad.baseline_from,
                bad.baseline_until,
                bad.current_from,
                bad.current_until,
                bad.report_sha256,
            ),
        )


def test_a_snapshot_carries_areas_without_their_notes(rollback: Any) -> None:
    area_id = insert_area(rollback, area_row(notes="private remark")).area_id
    table = next(one for one in SNAPSHOT_TABLES if one.name == "areas_of_interest")
    scope = SnapshotScope(since=T, as_of=datetime.now(UTC) + timedelta(minutes=5))
    rows = [
        one for one in read_table(rollback, table, scope) if one["area_id"] == area_id
    ]
    assert len(rows) == 1
    assert "notes" not in rows[0]
    assert rows[0]["geometry"]["type"] == "Polygon"

"""Regional alerts, and every attempt to deliver one — both append-only.

Reads and writes ``region_alerts`` and ``region_alert_deliveries``
(``deploy/migrations/sql/0022_regions.sql``). A regional alert is about a place,
not about Meridian: it shares no name with Prometheus's alert rules or with
Alertmanager, and nothing here is read by ``/metrics`` (Stage 32).

Reference: docs/DECISIONS.md D-231, D-232.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "NewRegionAlert",
    "StoredDelivery",
    "find_deliveries",
    "insert_delivery",
    "insert_region_alert",
]


@dataclass(frozen=True, slots=True)
class NewRegionAlert:
    """One alert in insertable form. ``recorded_at`` is the column's."""

    alert_id: str
    area_id: int
    quantity: str
    rule: str
    baseline_from: datetime
    baseline_until: datetime
    current_from: datetime
    current_until: datetime
    baseline_value: float
    current_value: float
    change: float
    change_low: float
    change_high: float
    threshold: float
    report_sha256: bytes
    summary: str


@dataclass(frozen=True, slots=True)
class StoredDelivery:
    """One delivery attempt as read back."""

    delivery_id: int
    alert_id: str
    channel: str
    outcome: str
    detail: str
    attempted_at: datetime


def insert_region_alert(conn: Connection, alert: NewRegionAlert) -> bool:
    """Record an alert. Returns False when it was already recorded.

    The id is derived from what the alert is about, so recording one report
    twice writes nothing the second time.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "insert into region_alerts (alert_id, area_id, quantity, rule,"
            " baseline_from, baseline_until, current_from, current_until,"
            " baseline_value, current_value, change, change_low, change_high,"
            " threshold, report_sha256, summary)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (alert_id) do nothing",
            (
                alert.alert_id,
                alert.area_id,
                alert.quantity,
                alert.rule,
                alert.baseline_from,
                alert.baseline_until,
                alert.current_from,
                alert.current_until,
                alert.baseline_value,
                alert.current_value,
                alert.change,
                alert.change_low,
                alert.change_high,
                alert.threshold,
                alert.report_sha256,
                alert.summary,
            ),
        )
        return cur.rowcount == 1


def insert_delivery(
    conn: Connection, alert_id: str, channel: str, outcome: str, detail: str
) -> None:
    """Append one delivery attempt."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "insert into region_alert_deliveries (alert_id, channel, outcome, detail)"
            " values (%s, %s, %s, %s)",
            (alert_id, channel, outcome, detail),
        )


def find_deliveries(conn: Connection, alert_id: str) -> list[StoredDelivery]:
    """Every attempt to deliver one alert, oldest first."""
    with conn.cursor(row_factory=class_row(StoredDelivery)) as cur:
        cur.execute(
            "select delivery_id, alert_id, channel, outcome, detail, attempted_at"
            " from region_alert_deliveries where alert_id = %s"
            " order by attempted_at, delivery_id",
            (alert_id,),
        )
        return cur.fetchall()

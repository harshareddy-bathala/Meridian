"""Regional alerts: stored as records, handed to a delivery interface (D-232).

A report's ``alerts.jsonl`` lists every change whose interval lay beyond its
threshold. Recording a report writes each as a ``region_alerts`` row and hands
it to an :class:`AlertDelivery`, appending what the delivery said to
``region_alert_deliveries``.

**Delivery is an interface, and the only implementation records.** Stage 29
delivers notifications and is not built, so :class:`RecordOnlyDelivery` sends
nothing and says so in the attempt it appends. Email, Telegram or anything else
arrives with Stage 29 as another implementation of the same protocol; nothing
here changes when it does.

**Not the platform's alerting.** Prometheus alert rules and Alertmanager watch
Meridian; these watch places. No name, table or metric is shared (Stage 32).

**Recording is idempotent.** An alert's id is derived from what it is about
and the snapshot and configuration it came from, so recording a report twice
writes no second alert — and appends no second delivery attempt for it.

Reference: docs/DECISIONS.md D-098, D-231, D-232.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.store.region_alerts import (
    NewRegionAlert,
    insert_delivery,
    insert_region_alert,
)
from meridian.store.stations import Connection

__all__ = [
    "AlertDelivery",
    "DeliveryOutcome",
    "RecordOnlyDelivery",
    "RecordingReport",
    "read_alerts",
    "record_alerts",
]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What one delivery attempt came to."""

    channel: str
    outcome: str
    """``recorded``, ``delivered`` or ``failed``."""

    detail: str


class AlertDelivery(Protocol):
    """Where an alert goes after it is recorded. Stage 29 adds the channels."""

    def deliver(self, alert: NewRegionAlert) -> DeliveryOutcome:
        """Send one alert, or say why it was not sent."""
        ...


class RecordOnlyDelivery:
    """The only delivery built: the alert is recorded and nothing is sent."""

    def deliver(self, alert: NewRegionAlert) -> DeliveryOutcome:
        """Record that no channel exists yet."""
        return DeliveryOutcome(
            channel="record_only",
            outcome="recorded",
            detail=(
                f"{alert.alert_id} recorded; no delivery channel exists until "
                "Stage 29 builds notifications"
            ),
        )


@dataclass(frozen=True, slots=True)
class RecordingReport:
    """What recording one report did."""

    alerts: int
    written: int


def read_alerts(report_dir: Path) -> list[NewRegionAlert]:
    """The alerts a published report lists, ready to record.

    Args:
        report_dir: The report's directory. Verified before it is read.

    Raises:
        DamagedSnapshotError: The directory is not what its manifest says.
        ValueError: It is not a regional report.
    """
    report = read_directory(report_dir)
    if report.manifest.kind != "regions_report":
        message = f"{report_dir} is a {report.manifest.kind}, not a regional report"
        raise ValueError(message)
    sha = content_sha256(report.manifest)
    lines = report.files["alerts.jsonl"].decode("utf-8").splitlines()
    return [_alert(json.loads(line), sha) for line in lines if line.strip()]


def record_alerts(
    conn: Connection, alerts: list[NewRegionAlert], delivery: AlertDelivery
) -> RecordingReport:
    """Record each alert and hand each new one to ``delivery``.

    An alert already recorded is neither written nor delivered again.
    """
    written = 0
    for alert in alerts:
        if not insert_region_alert(conn, alert):
            continue
        written += 1
        outcome = delivery.deliver(alert)
        insert_delivery(
            conn, alert.alert_id, outcome.channel, outcome.outcome, outcome.detail
        )
    return RecordingReport(alerts=len(alerts), written=written)


def _alert(row: dict[str, object], report_sha: bytes) -> NewRegionAlert:
    rule = _mapping(row["rule"])
    baseline, current = _mapping(row["baseline"]), _mapping(row["current"])
    change = float(str(row["change"]))
    summary = (
        f"area {row['area_id']}: {row['quantity']} changed {change:+.3f} "
        f"({rule['kind']}), interval [{float(str(row['low'])):+.3f}, "
        f"{float(str(row['high'])):+.3f}], beyond {float(str(rule['threshold'])):+g}"
    )
    return NewRegionAlert(
        alert_id=str(row["alert_id"]),
        area_id=int(str(row["area_id"])),
        quantity=str(row["quantity"]),
        rule=str(rule["kind"]),
        baseline_from=_instant(baseline["from"]),
        baseline_until=_instant(baseline["until"]),
        current_from=_instant(current["from"]),
        current_until=_instant(current["until"]),
        baseline_value=float(str(row["baseline_mean"])),
        current_value=float(str(row["current_mean"])),
        change=change,
        change_low=float(str(row["low"])),
        change_high=float(str(row["high"])),
        threshold=float(str(rule["threshold"])),
        report_sha256=report_sha,
        summary=summary,
    )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"expected an object, found {value!r}"
        raise TypeError(message)
    return {str(key): item for key, item in value.items()}


def _instant(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

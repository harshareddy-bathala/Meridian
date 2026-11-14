"""The network's week, read from the store into the facts its report is made of.

The passes are the stored classifications whose window closed in the week,
under the deployment's method and configuration, counted by the same rules the
reliability report and the live page use: ``capture_rate``, ``loss_budget`` and
``class_counts`` (D-182, D-362). Nothing here restates what is captured or
lost.

**A week is reported once it has settled**: Monday to Monday UTC, and its last
passes past the classification's settle margin (D-306), so its figures no
longer change as rounds classify them.

**Read as the records stand.** A network report is not queued or sent, so it
names no rows the way an owner's does (D-302). Its ``inputs_sha256`` says
whether a second read found the same facts; append-only rows mean only a late
diagnosis or a removed station can change a settled week.

Reference: docs/DECISIONS.md D-182, D-302, D-306, D-362, D-367.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from meridian.notifications.compose import last_week
from meridian.notifications.network import NetworkWeeklyFacts, PopulationWeek
from meridian.notifications.reading import WEEK
from meridian.reliability.budget import loss_budget
from meridian.reliability.classification import METHOD as CLASSIFICATION_METHOD
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.daily import class_counts
from meridian.reliability.diagnosis import METHOD as DIAGNOSIS_METHOD
from meridian.reliability.slis import PassRecord, capture_rate
from meridian.store.network_week_reads import (
    count_causes_between,
    count_stations,
    count_stations_heard,
    count_warnings_open,
)
from meridian.store.reliability_reads import ClassifiedRow, find_classified_between
from meridian.store.stations import Connection

__all__ = ["INTERVAL_PLACES", "latest_settled_week", "network_week_facts"]

INTERVAL_PLACES = 4
"""Decimal places an interval bound keeps, so its hash is stable across
platforms' last bits of floating point."""


def latest_settled_week(now: datetime, config: ReliabilityConfig) -> date:
    """The Monday that starts the latest week settled by ``now``."""
    return last_week(now, config.classification.settle_margin_s).date()


def network_week_facts(
    conn: Connection, monday: date, config: ReliabilityConfig
) -> NetworkWeeklyFacts:
    """The network's week from ``monday``, as the store holds it now.

    Args:
        conn: An open connection. Read-only.
        monday: The week's first day.
        config: The deployment's reliability configuration, which names the
            classification and diagnosis read and SC-4's target.
    """
    start = datetime.combine(monday, time(), UTC)
    end = start + WEEK
    classified_under = (CLASSIFICATION_METHOD, config.classification.sha256())
    rows = find_classified_between(
        conn, classified_under=classified_under, window=(start, end)
    )
    causes = count_causes_between(
        conn,
        classified_under=classified_under,
        diagnosed_under=(DIAGNOSIS_METHOD, config.diagnosis.sha256()),
        window=(start, end),
    )
    stations = count_stations(conn, end)
    heard = count_stations_heard(conn, start, end)
    warnings = count_warnings_open(conn, end)
    target = config.slo.capture_rate_min

    def population(simulated: bool) -> PopulationWeek:
        passes = _records(row for row in rows if row.simulated is simulated)
        capture = capture_rate(passes)
        budget = loss_budget(passes, capture_target=target)
        interval = capture.interval
        return PopulationWeek(
            simulated=simulated,
            stations=stations.get(simulated, 0),
            stations_heard=heard.get(simulated, 0),
            passes=len(passes),
            classes=tuple((key, n) for key, n in class_counts(passes).items() if n),
            captured=capture.numerator,
            eligible=capture.denominator,
            capture_low=None if interval is None else _bound(interval[0]),
            capture_high=None if interval is None else _bound(interval[1]),
            lost=budget.spent,
            allowed=budget.allowed,
            causes=tuple((cause, n) for sim, cause, n in causes if sim is simulated),
            warnings_open=warnings.get(simulated, 0),
        )

    return NetworkWeeklyFacts(
        period_start=start,
        period_end=end,
        classification_method=CLASSIFICATION_METHOD,
        classification_sha256=config.classification.sha256().hex(),
        capture_target=target,
        measured=population(False),
        simulated=population(True),
    )


def _records(rows: Iterable[ClassifiedRow]) -> list[PassRecord]:
    return [
        PassRecord(
            reference=row.assignment_id,
            station_id=row.station_id,
            window_end=row.window_end,
            classification=row.classification,
            listening_confirmed=row.listening_confirmed,
            outcome=row.outcome,
            simulated=row.simulated,
        )
        for row in rows
    ]


def _bound(value: float) -> float:
    return round(value, INTERVAL_PLACES)

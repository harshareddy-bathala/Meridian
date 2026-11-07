"""The network's live aggregates: today's passes, and a week by day.

What ``/api/v1/aggregates`` serves for the dashboard's live page (D-362). Two
parts, for measured and simulated stations apart (rule 5):

- **Today**, the UTC day so far: every pass scheduled to close today, how many
  have closed, how many of those are classified and by class, how many of them
  had a station confirmed listening, and how many have closed and are not
  classified yet. A pass is classified only after the settle margin (D-182), so
  on a deployment with the default day almost nothing closing today is, and
  the body says how many are waiting rather than reading as quiet.
- **The week**, the last seven UTC days with today the last: each day's capture
  rate and loss budget, and whether the day has settled, meaning its last
  window closed more than the settle margin ago. An unsettled day's figures can
  still change.

Counted from the stored classifications under the deployment's method and
configuration, as the reliability report is (D-184).

Reference: docs/DECISIONS.md D-182, D-184, D-185, D-362.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from meridian.reliability.classification import METHOD
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.daily import (
    DayFigures,
    class_counts,
    day_bounds,
    figures_by_day,
)
from meridian.reliability.slis import PassRecord
from meridian.store.reliability_reads import (
    count_scheduled_between,
    find_classified_between,
)
from meridian.store.stations import Connection

__all__ = [
    "DAYS",
    "NetworkAggregates",
    "PopulationAggregates",
    "SettledDay",
    "Today",
    "read_live_aggregates",
]

DAYS = 7
"""A week, today included."""


@dataclass(frozen=True, slots=True)
class Today:
    """One population's passes closing on the UTC day so far."""

    day: date
    scheduled: int
    """Scheduled to close today, including those still to come."""

    ended: int
    classified: int
    by_class: dict[str, int]
    listening_confirmed: int
    """Classified passes with a station confirmed listening (rule 7)."""

    awaiting: int
    """Closed, and not classified yet."""


@dataclass(frozen=True, slots=True)
class SettledDay:
    """One day's figures, and whether they can still change."""

    figures: DayFigures
    settled: bool


@dataclass(frozen=True, slots=True)
class PopulationAggregates:
    """Measured or simulated stations' aggregates, never pooled."""

    simulated: bool
    today: Today
    days: tuple[SettledDay, ...]


@dataclass(frozen=True, slots=True)
class NetworkAggregates:
    """Both populations' aggregates, and what they were counted under."""

    as_of: datetime
    method: str
    config_sha256: str
    settle_margin_s: int
    capture_target: float
    measured: PopulationAggregates
    simulated: PopulationAggregates


def read_live_aggregates(
    conn: Connection, *, now: datetime, config: ReliabilityConfig
) -> NetworkAggregates:
    """Count today's passes and the week's figures, as of ``now``.

    Args:
        conn: An open connection. Read-only.
        now: The instant counted to, timezone-aware UTC.
        config: The classification it reads and SC-4's target.

    Returns:
        Both populations' aggregates.
    """
    today = now.astimezone(UTC).date()
    days = [today - timedelta(days=back) for back in range(DAYS - 1, -1, -1)]
    classified_under = (METHOD, config.classification.sha256())
    rows = find_classified_between(
        conn,
        classified_under=classified_under,
        window=(day_bounds(days[0])[0], now),
    )
    scheduled = count_scheduled_between(
        conn, classified_under=classified_under, window=day_bounds(today), now=now
    )
    margin = timedelta(seconds=config.classification.settle_margin_s)
    target = config.slo.capture_rate_min

    def population(simulated: bool) -> PopulationAggregates:
        passes = [
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
            if row.simulated is simulated
        ]
        counts = scheduled.get(simulated)
        todays = [p for p in passes if p.window_end.astimezone(UTC).date() == today]
        return PopulationAggregates(
            simulated=simulated,
            today=Today(
                day=today,
                scheduled=counts.scheduled if counts else 0,
                ended=counts.ended if counts else 0,
                classified=len(todays),
                by_class=class_counts(todays),
                listening_confirmed=sum(p.listening_confirmed for p in todays),
                awaiting=counts.awaiting if counts else 0,
            ),
            days=_settled(
                figures_by_day(passes, days=days, capture_target=target),
                now=now,
                margin=margin,
            ),
        )

    return NetworkAggregates(
        as_of=now,
        method=METHOD,
        config_sha256=config.classification.sha256().hex(),
        settle_margin_s=config.classification.settle_margin_s,
        capture_target=target,
        measured=population(False),
        simulated=population(True),
    )


def _settled(
    figures: Sequence[DayFigures], *, now: datetime, margin: timedelta
) -> tuple[SettledDay, ...]:
    return tuple(
        SettledDay(figures=one, settled=day_bounds(one.day)[1] + margin <= now)
        for one in figures
    )

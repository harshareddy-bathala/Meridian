"""The reliability report, counted from the live record.

Reads the passes classified inside the SLO window from ``pass_classifications``
(D-182), each station's heartbeat-covered seconds, and each report's delay, and
hands them to :func:`~meridian.reliability.report.build_report`, the same
assembly the snapshot command uses.

**The window lags by the settle margin.** A pass is classified only once its
report has had time to arrive, so the most recent day of passes is not in any
figure yet. The report states the window it counted.

Reference: docs/DECISIONS.md D-182, D-184, D-185.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from meridian.registry.liveness import OFFLINE_AFTER_S
from meridian.reliability.classification import METHOD
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.report import ReliabilityReport, build_report
from meridian.reliability.slis import PassRecord, Share
from meridian.store.reliability_reads import (
    find_classified_between,
    find_station_online_seconds,
    find_submission_delays,
)
from meridian.store.stations import Connection

__all__ = ["read_live_report"]


def read_live_report(
    conn: Connection, *, now: datetime, config: ReliabilityConfig
) -> ReliabilityReport:
    """Count the live report for the window ending at ``now``.

    Args:
        conn: An open connection. Read-only.
        now: The window's end, timezone-aware UTC.
        config: The classification it reads and the targets it judges by.

    Returns:
        Both populations' figures over ``[now − window_days, now)``.
    """
    window = (now - timedelta(days=config.slo.window_days), now)
    classified_under = (METHOD, config.classification.sha256())
    rows = find_classified_between(
        conn, classified_under=classified_under, window=window
    )
    online = find_station_online_seconds(
        conn, window=window, offline_after_s=OFFLINE_AFTER_S
    )
    delays: dict[bool, list[float]] = defaultdict(list)
    for one in find_submission_delays(
        conn, classified_under=classified_under, window=window
    ):
        delays[one.simulated].append(one.delay_s)
    return build_report(
        (
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
        ),
        header=("live", window, METHOD, config.classification.sha256().hex()),
        slo=config.slo,
        availability={
            one.station_id: (one.simulated, Share(one.covered_s, one.span_s))
            for one in online
        },
        submission_delays=dict(delays),
    )

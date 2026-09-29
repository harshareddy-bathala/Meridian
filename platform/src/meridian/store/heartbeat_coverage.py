"""How much of each hour a station was heard from, read from ``heartbeats_hourly``.

The continuous aggregate (migration 0022, D-178) serves the reads that need
coverage rather than evidence. Whether a station was listening for one pass is
``Registry.was_listening``'s question, and that reads raw heartbeats and nothing
else (rule 7).

Every hour of the span is returned, an hour with no heartbeat as zero, so a
reader sees the gap instead of a shorter list.

Reference: docs/DECISIONS.md D-178.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["HourlyCoverage", "find_hourly_coverage"]


@dataclass(frozen=True, slots=True)
class HourlyCoverage:
    """One hour of one station's heartbeats."""

    hour: datetime
    heartbeats: int
    listening: int


def find_hourly_coverage(
    conn: Connection, station_id: str, *, start: datetime, end: datetime
) -> list[HourlyCoverage]:
    """Every hour in ``[start, end)``, oldest first, zero where nothing arrived.

    Args:
        conn: An open connection. Read-only.
        station_id: The station.
        start: The first hour, on the hour.
        end: The hour after the last, on the hour.
    """
    with conn.cursor(row_factory=class_row(HourlyCoverage)) as cur:
        cur.execute(
            "select span.hour, coalesce(sum(h.heartbeats), 0)::int as heartbeats,"
            " coalesce(sum(h.listening), 0)::int as listening"
            " from generate_series(%(start)s::timestamptz,"
            "  %(end)s::timestamptz - interval '1 hour', interval '1 hour')"
            "  as span(hour)"
            " left join heartbeats_hourly h"
            "  on h.hour = span.hour and h.station_id = %(station)s"
            " group by span.hour order by span.hour",
            {"station": station_id, "start": start, "end": end},
        )
        return cur.fetchall()

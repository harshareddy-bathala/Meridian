"""The live reliability report, from rows in real TimescaleDB.

Station availability and submission delay are read by SQL, so their arithmetic
is checked here against seconds worked out by hand. The last tests run the
accounting and then the report, and count the report's figures back from the
stored rows.

Reference: docs/DECISIONS.md D-182, D-184, D-185.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.registry.liveness import OFFLINE_AFTER_S
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.reliability.accounting import classify_settled
from meridian.reliability.classification import METHOD
from meridian.reliability.config import (
    ClassificationConfig,
    ReliabilityConfig,
)
from meridian.reliability.live import read_live_report
from meridian.store.reliability_reads import (
    find_station_online_seconds,
    find_submission_delays,
)

pytestmark = pytest.mark.integration

T = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
HOUR = (T, T + timedelta(hours=1))
SATELLITE = "norad:99970"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


def station(rows: Any, station_id: str, *, registered: datetime, **kw: Any) -> None:
    rows.station(station_id, **kw)
    with rows.conn.cursor() as cur:
        cur.execute(
            "update stations set registered_at = %s where station_id = %s",
            (registered, station_id),
        )


def beats(conn: Any, station_id: str, *instants: datetime) -> None:
    with conn.cursor() as cur:
        for at in instants:
            cur.execute(
                "insert into heartbeats (station_id, sent_at, received_at, state,"
                " simulated) select %s, %s, %s, 'idle', simulated from stations"
                " where station_id = %s",
                (station_id, at, at, station_id),
            )


def online(conn: Any, window: tuple[datetime, datetime] = HOUR) -> dict[str, Any]:
    return {
        one.station_id: one
        for one in find_station_online_seconds(
            conn, window=window, offline_after_s=OFFLINE_AFTER_S
        )
    }


def seconds(*offsets: float) -> tuple[datetime, ...]:
    return tuple(T + timedelta(seconds=one) for one in offsets)


def test_a_heartbeat_vouches_until_the_next_or_the_offline_threshold(
    schedule_rows: Any,
) -> None:
    """Beats at 0, 30 and 60 s: 30 + 30 + 90 seconds covered of 3600."""
    station(schedule_rows, "st_a", registered=T - timedelta(days=1), simulated=False)
    beats(schedule_rows.conn, "st_a", *seconds(0, 30, 60))

    held = online(schedule_rows.conn)["st_a"]

    assert (held.covered_s, held.span_s, held.simulated) == (150.0, 3600.0, False)


def test_a_gap_past_the_threshold_is_offline_for_the_rest_of_it(
    schedule_rows: Any,
) -> None:
    """A 600 s gap covers 90 s of itself, not 600."""
    station(schedule_rows, "st_a", registered=T - timedelta(days=1))
    beats(schedule_rows.conn, "st_a", *seconds(0, 600))

    assert online(schedule_rows.conn)["st_a"].covered_s == 180.0


def test_a_heartbeat_just_before_the_window_covers_its_start(
    schedule_rows: Any,
) -> None:
    station(schedule_rows, "st_a", registered=T - timedelta(days=1))
    beats(schedule_rows.conn, "st_a", *seconds(-60))

    assert online(schedule_rows.conn)["st_a"].covered_s == 30.0


def test_a_station_is_measured_from_its_registration(schedule_rows: Any) -> None:
    station(schedule_rows, "st_new", registered=T + timedelta(minutes=30))
    station(schedule_rows, "st_silent", registered=T - timedelta(days=1))

    held = online(schedule_rows.conn)

    assert held["st_new"].span_s == 1800.0
    assert (held["st_silent"].covered_s, held["st_silent"].span_s) == (0.0, 3600.0)


def test_a_deleted_or_not_yet_registered_station_is_not_measured(
    schedule_rows: Any,
) -> None:
    station(schedule_rows, "st_gone", registered=T - timedelta(days=1), deleted=True)
    station(schedule_rows, "st_later", registered=T + timedelta(hours=2))

    assert not {"st_gone", "st_later"} & set(online(schedule_rows.conn))


def classified_world(rows: Any) -> None:
    """Two measured passes (a decode and a confirmed miss) and a simulated decode.

    Settled by ``LATER``; the decode's report arrived ten minutes after its
    window closed.
    """
    element_set = rows.satellite(SATELLITE)
    station(rows, "st_a", registered=T - timedelta(days=1), simulated=False)
    station(rows, "st_b", registered=T - timedelta(days=1), simulated=False)
    station(rows, "st_sim", registered=T - timedelta(days=1), simulated=True)
    for station_id, assignment, hours, outcome in (
        ("st_a", "as_1", 0, "decoded"),
        ("st_b", "as_2", 1, "no_signal"),
        ("st_sim", "as_3", 2, "decoded"),
    ):
        aos = T + timedelta(hours=hours)
        rows.assignment(
            assignment,
            rows.pass_(station_id, aos, element_set_id=element_set),
            state="reported",
        )
        rows.observation(assignment, outcome=outcome)
    with rows.conn.cursor() as cur:
        cur.execute(
            "update observations set submitted_at = ended_at + interval '10 minutes'"
        )
        cur.execute(
            "insert into heartbeats (station_id, sent_at, received_at, state,"
            " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
            " listening_mode) values ('st_b', %s, %s, 'listening', 'as_2', %s,"
            " 137900000, 'lrpt')",
            (T + timedelta(hours=1, minutes=3),) * 2 + (SATELLITE,),
        )


LATER = T + timedelta(days=3)


def classify(conn: Any) -> None:
    registry = PsycopgRegistry(
        conn, pepper="test-pepper", recovery_window_s=3600, now_utc=LATER
    )
    classify_settled(
        conn, registry, now=LATER, config=ReliabilityConfig().classification
    )


def test_the_report_counts_what_the_record_holds(schedule_rows: Any) -> None:
    classified_world(schedule_rows)
    classify(schedule_rows.conn)

    report = read_live_report(schedule_rows.conn, now=LATER, config=ReliabilityConfig())

    measured, simulated = report.measured, report.simulated
    assert (measured.passes, simulated.passes) == (2, 1)
    assert (measured.capture.numerator, measured.capture.denominator) == (1, 2)
    assert measured.confirmed_miss.numerator == 1
    assert [one.reason for one in measured.budget.debits] == ["confirmed_miss"]
    assert [one.reference for one in measured.budget.debits] == ["as_2"]
    assert simulated.budget.debits == ()
    assert report.method == METHOD


def test_every_report_arrived_ten_minutes_after_its_window(schedule_rows: Any) -> None:
    classified_world(schedule_rows)
    classify(schedule_rows.conn)
    config = ReliabilityConfig()

    delays = find_submission_delays(
        schedule_rows.conn,
        classified_under=(METHOD, config.classification.sha256()),
        window=(LATER - timedelta(days=30), LATER),
    )

    assert sorted((one.simulated, one.delay_s) for one in delays) == [
        (False, 600.0),
        (False, 600.0),
        (True, 600.0),
    ]


def test_a_pass_classified_under_other_parameters_is_not_counted(
    schedule_rows: Any,
) -> None:
    """The report reads one method and configuration, never a mixture."""
    classified_world(schedule_rows)
    classify(schedule_rows.conn)
    other = ReliabilityConfig(classification=ClassificationConfig(silent_window_s=60))

    report = read_live_report(schedule_rows.conn, now=LATER, config=other)

    assert report.measured.passes == 0

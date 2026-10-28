"""Owner reports' two tables — what the store and schema allow.

Marked ``integration`` by the directory hook. What only a database shows:
- a report is queued once per subject and channel, whatever its template;
- a report takes ``simulated`` from its assignment or station, never the caller;
- a simulated report is refused on any channel but the sink (D-301);
- a week starts on a Monday at 00:00 UTC, and a template names its own kind;
- a report is due until it is sent or abandoned, and a failed attempt makes it
  due again only once its retry time has come;
- a report ends once, and an attempt's parts fit its outcome.

Reference: docs/DECISIONS.md D-300, D-301, D-305.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.report_deliveries import (  # noqa: E402
    NewPassReport,
    NewWeeklyReport,
    due_deliveries,
    find_attempts,
    find_deliveries,
    find_delivery,
    queue_pass_report,
    queue_weekly_report,
    record_attempt,
)

pytestmark = pytest.mark.integration

AOS = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
MONDAY = datetime(2026, 10, 12, tzinfo=UTC)
NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
INPUTS_SHA = bytes([1]) * 32
CONTENT_SHA = bytes([2]) * 32


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def assignment(schedule_rows: Any, name: str, *, simulated: bool) -> str:
    """One assignment on a station of its own."""
    station = schedule_rows.station(f"st-{name}", simulated=simulated)
    held = schedule_rows.conn.execute(
        "select id from element_sets where satellite_id = 'norad:99970' limit 1"
    ).fetchone()
    element_set = held[0] if held else schedule_rows.satellite()
    pass_id = schedule_rows.pass_(station, AOS, element_set_id=element_set)
    return str(schedule_rows.assignment(f"a-{name}", pass_id))


def pass_report(assignment_id: str, **changes: Any) -> NewPassReport:
    return replace(
        NewPassReport(
            assignment_id=assignment_id,
            channel="sink",
            template_version="pass-1",
            inputs={"assignment_id": assignment_id, "revision": 1},
            inputs_sha256=INPUTS_SHA,
            content_sha256=CONTENT_SHA,
        ),
        **changes,
    )


def weekly_report(station_id: str, **changes: Any) -> NewWeeklyReport:
    return replace(
        NewWeeklyReport(
            station_id=station_id,
            period_start=MONDAY,
            channel="sink",
            template_version="weekly-1",
            inputs={"receptions": [], "warnings": []},
            inputs_sha256=INPUTS_SHA,
            content_sha256=CONTENT_SHA,
        ),
        **changes,
    )


def test_a_pass_is_queued_once_per_channel_whatever_its_template(
    rollback: Any, schedule_rows: Any
) -> None:
    subject = assignment(schedule_rows, "once", simulated=False)

    first = queue_pass_report(rollback, pass_report(subject))
    again = queue_pass_report(rollback, pass_report(subject, template_version="pass-2"))
    elsewhere = queue_pass_report(rollback, pass_report(subject, channel="email"))

    assert first is not None
    assert again is None
    assert elsewhere is not None and elsewhere != first


def test_a_week_is_queued_once_per_station_and_channel(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-week-once", simulated=False)

    first = queue_weekly_report(rollback, weekly_report(station))
    again = queue_weekly_report(rollback, weekly_report(station))
    later = queue_weekly_report(
        rollback, weekly_report(station, period_start=MONDAY + timedelta(days=7))
    )

    assert first is not None
    assert again is None
    assert later is not None


def test_a_report_takes_simulated_from_what_it_is_about(
    rollback: Any, schedule_rows: Any
) -> None:
    measured = assignment(schedule_rows, "measured", simulated=False)
    simulated = assignment(schedule_rows, "simulated", simulated=True)

    on_measured = queue_pass_report(rollback, pass_report(measured))
    on_simulated = queue_pass_report(rollback, pass_report(simulated))
    week = queue_weekly_report(rollback, weekly_report("st-simulated"))

    assert on_measured is not None and on_simulated is not None and week is not None
    assert find_delivery(rollback, on_measured).simulated is False  # type: ignore[union-attr]
    assert find_delivery(rollback, on_simulated).simulated is True  # type: ignore[union-attr]
    assert find_delivery(rollback, week).simulated is True  # type: ignore[union-attr]
    assert find_delivery(rollback, week).station_id == "st-simulated"  # type: ignore[union-attr]


@pytest.mark.parametrize("channel", ["email", "telegram"])
def test_a_simulated_report_goes_only_to_the_sink(
    rollback: Any, schedule_rows: Any, channel: str
) -> None:
    subject = assignment(schedule_rows, f"sim-{channel}", simulated=True)

    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        queue_pass_report(rollback, pass_report(subject, channel=channel))
    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        queue_weekly_report(
            rollback, weekly_report(f"st-sim-{channel}", channel=channel)
        )


def test_a_measured_report_may_go_to_a_person(
    rollback: Any, schedule_rows: Any
) -> None:
    subject = assignment(schedule_rows, "to-person", simulated=False)

    assert queue_pass_report(rollback, pass_report(subject, channel="email"))
    assert queue_pass_report(rollback, pass_report(subject, channel="telegram"))


@pytest.mark.parametrize(
    "start",
    [MONDAY + timedelta(days=1), MONDAY + timedelta(hours=6)],
    ids=["tuesday", "monday-morning"],
)
def test_a_week_starts_at_monday_midnight_utc(
    rollback: Any, schedule_rows: Any, start: datetime
) -> None:
    station = schedule_rows.station("st-week-start", simulated=False)

    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        queue_weekly_report(rollback, weekly_report(station, period_start=start))


def test_a_template_names_the_kind_of_report_it_renders(
    rollback: Any, schedule_rows: Any
) -> None:
    subject = assignment(schedule_rows, "kind", simulated=False)

    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        queue_pass_report(rollback, pass_report(subject, template_version="weekly-1"))


def test_no_column_could_hold_a_recipient(rollback: Any) -> None:
    columns = {
        row[0]
        for row in rollback.execute(
            "select column_name from information_schema.columns"
            " where table_name in ('report_deliveries', 'report_delivery_attempts')"
        ).fetchall()
    }

    assert not {"address", "recipient", "email", "chat_id", "error"} & columns


def test_a_report_is_due_until_it_ends(rollback: Any, schedule_rows: Any) -> None:
    subject = assignment(schedule_rows, "due", simulated=False)
    report = queue_pass_report(rollback, pass_report(subject))
    assert report is not None
    retry = NOW + timedelta(minutes=2)

    assert [one.id for one in due_deliveries(rollback, NOW)] == [report]
    record_attempt(
        rollback,
        report,
        attempted_at=NOW,
        outcome="failed",
        error_class="timeout",
        next_attempt_at=retry,
    )
    assert due_deliveries(rollback, NOW + timedelta(minutes=1)) == []
    assert [one.id for one in due_deliveries(rollback, retry)] == [report]
    assert find_delivery(rollback, report).state == "retrying"  # type: ignore[union-attr]

    record_attempt(rollback, report, attempted_at=retry, outcome="sent")

    assert due_deliveries(rollback, retry + timedelta(days=1)) == []
    stored = find_delivery(rollback, report)
    assert stored is not None
    assert (stored.state, stored.attempts) == ("sent", 2)
    assert [one.outcome for one in find_attempts(rollback, report)] == [
        "failed",
        "sent",
    ]


def test_an_abandoned_report_is_never_due_again(
    rollback: Any, schedule_rows: Any
) -> None:
    subject = assignment(schedule_rows, "abandoned", simulated=False)
    report = queue_pass_report(rollback, pass_report(subject))
    assert report is not None

    record_attempt(
        rollback, report, attempted_at=NOW, outcome="abandoned", error_class="refused"
    )

    assert due_deliveries(rollback, NOW + timedelta(days=30)) == []
    assert find_delivery(rollback, report).state == "abandoned"  # type: ignore[union-attr]


def test_a_report_ends_once(rollback: Any, schedule_rows: Any) -> None:
    subject = assignment(schedule_rows, "ends-once", simulated=False)
    report = queue_pass_report(rollback, pass_report(subject))
    assert report is not None
    record_attempt(rollback, report, attempted_at=NOW, outcome="sent")

    with pytest.raises(psycopg.errors.UniqueViolation), rollback.transaction():
        record_attempt(rollback, report, attempted_at=NOW, outcome="sent")
    with pytest.raises(psycopg.errors.UniqueViolation), rollback.transaction():
        record_attempt(
            rollback,
            report,
            attempted_at=NOW,
            outcome="abandoned",
            error_class="refused",
        )


@pytest.mark.parametrize(
    "attempt",
    [
        {"outcome": "sent", "error_class": "timeout"},
        {"outcome": "failed", "error_class": "timeout"},
        {"outcome": "failed", "next_attempt_at": NOW + timedelta(minutes=1)},
        {
            "outcome": "failed",
            "error_class": "timeout",
            "next_attempt_at": NOW - timedelta(minutes=1),
        },
        {"outcome": "abandoned"},
        {"outcome": "failed", "error_class": "Server said: no such user a@b.c"},
    ],
    ids=[
        "sent-with-error",
        "failed-without-retry",
        "failed-without-class",
        "retry-in-the-past",
        "abandoned-without-class",
        "error-as-words",
    ],
)
def test_an_attempt_must_fit_its_outcome(
    rollback: Any, schedule_rows: Any, attempt: dict[str, Any]
) -> None:
    subject = assignment(schedule_rows, "shape", simulated=False)
    report = queue_pass_report(rollback, pass_report(subject))
    assert report is not None

    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        record_attempt(rollback, report, attempted_at=NOW, **attempt)


def test_reports_are_listed_newest_first_for_one_station(
    rollback: Any, schedule_rows: Any
) -> None:
    first = assignment(schedule_rows, "list-a", simulated=False)
    other = assignment(schedule_rows, "list-b", simulated=False)
    mine = queue_pass_report(rollback, pass_report(first))
    week = queue_weekly_report(rollback, weekly_report("st-list-a"))
    queue_pass_report(rollback, pass_report(other))

    listed = find_deliveries(rollback, station_id="st-list-a")

    assert {one.id for one in listed} == {mine, week}
    assert all(one.state == "queued" and one.attempts == 0 for one in listed)

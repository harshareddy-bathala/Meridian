"""The network's figures by UTC day, and what each class does to SC-4 (D-362, D-365).

``figures_by_day`` must apply the reliability report's own rules to one day at a
time, and nothing of its own: a day's capture rate and budget are what
``capture_rate`` and ``loss_budget`` give for that day's passes alone.

Reference: docs/DECISIONS.md D-184, D-185, D-362.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from meridian.reliability.budget import DEBIT_REASONS, effect_of, loss_budget
from meridian.reliability.classification import PASS_CLASSES
from meridian.reliability.daily import class_counts, day_bounds, figures_by_day
from meridian.reliability.slis import PassRecord, capture_rate

DAY = date(2026, 9, 1)


def record(end: datetime, classification: str, reference: str = "as_1") -> PassRecord:
    return PassRecord(
        reference=reference,
        station_id="st_1",
        window_end=end,
        classification=classification,
        listening_confirmed=classification == "confirmed_miss",
        outcome=None,
        simulated=False,
    )


def at(day: date, hour: int) -> datetime:
    return day_bounds(day)[0] + timedelta(hours=hour)


def test_a_pass_belongs_to_the_utc_day_its_window_closed() -> None:
    # 23:30 at UTC+05:30 is 18:00 UTC the same day; 01:00 at UTC+05:30 is the
    # previous UTC day.
    ist = timezone(timedelta(hours=5, minutes=30))
    passes = [
        record(datetime(2026, 9, 1, 23, 30, tzinfo=ist), "successful_reception"),
        record(datetime(2026, 9, 2, 1, 0, tzinfo=ist), "confirmed_miss", "as_2"),
    ]

    (one,) = figures_by_day(passes, days=[DAY], capture_target=0.9)

    assert one.passes == 2


def test_each_day_is_counted_by_the_reports_own_rules() -> None:
    first = [
        record(at(DAY, 1), "successful_reception"),
        record(at(DAY, 2), "station_unavailable", "as_2"),
        record(at(DAY, 3), "satellite_silent", "as_3"),
    ]
    second = [record(at(DAY + timedelta(days=1), 4), "confirmed_miss", "as_4")]

    days = figures_by_day(
        first + second, days=[DAY, DAY + timedelta(days=1)], capture_target=0.9
    )

    assert days[0].capture == capture_rate(first)
    assert days[0].budget == loss_budget(first, capture_target=0.9)
    assert days[1].capture == capture_rate(second)
    assert days[0].passes == 3


def test_a_day_with_no_pass_is_still_reported_with_nothing_counted() -> None:
    (empty,) = figures_by_day([], days=[DAY], capture_target=0.9)

    assert empty.day == DAY
    assert (empty.passes, empty.capture.denominator, empty.capture.estimate) == (
        0,
        0,
        None,
    )


def test_a_pass_on_a_day_not_asked_for_is_left_out() -> None:
    passes = [record(at(DAY - timedelta(days=1), 12), "successful_reception")]

    (one,) = figures_by_day(passes, days=[DAY], capture_target=0.9)

    assert one.passes == 0


def test_every_class_is_counted_even_when_none_happened() -> None:
    counts = class_counts([record(at(DAY, 1), "signal_no_decode")])

    assert list(counts) == list(PASS_CLASSES)
    assert counts["signal_no_decode"] == 1
    assert sum(counts.values()) == 1


def test_a_day_runs_from_midnight_to_midnight_utc() -> None:
    start, end = day_bounds(DAY)

    assert (start, end) == (
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
    )


def test_every_class_has_one_effect_and_only_debits_are_lost() -> None:
    effects = {one: effect_of(one) for one in PASS_CLASSES}

    assert effects["successful_reception"] == "captured"
    assert {k for k, v in effects.items() if v == "satellite"} == {
        "satellite_silent",
        "satellite_state_indeterminate",
    }
    assert tuple(k for k, v in effects.items() if v == "lost") == DEBIT_REASONS

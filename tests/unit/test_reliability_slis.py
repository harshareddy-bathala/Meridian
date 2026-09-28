"""The indicators and the budget, on passes counted by hand.

Every expected number is written out from the passes in the test, so a
reader can check it without running anything.

Reference: docs/DECISIONS.md D-184, D-185.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.reliability.budget import DEBIT_REASONS, loss_budget
from meridian.reliability.slis import (
    PassRecord,
    Proportion,
    Share,
    assignment_completion_rate,
    capture_rate,
    confirmed_miss_rate,
    delays,
    schedule_execution_rate,
)

END = datetime(2026, 9, 20, tzinfo=UTC)


def record(
    classification: str,
    *,
    listening: bool = True,
    outcome: str | None = "no_signal",
    n: int = 0,
    station: str = "st_a",
) -> PassRecord:
    return PassRecord(
        reference=f"as_{n}",
        station_id=station,
        window_end=END + timedelta(hours=n),
        classification=classification,
        listening_confirmed=listening,
        outcome=outcome,
        simulated=False,
    )


FLEET = [
    record("successful_reception", outcome="decoded", n=1),
    record("successful_reception", outcome="decoded", n=2),
    record("successful_reception", outcome="decoded", listening=False, n=3),
    record("signal_no_decode", outcome="signal_no_decode", n=4),
    record("confirmed_miss", n=5),
    record("station_unavailable", listening=False, outcome=None, n=6),
    record("station_unavailable", listening=False, outcome="not_attempted", n=7),
    record("station_not_confirmed_listening", listening=False, n=8),
    record("assignment_declined", listening=False, outcome=None, n=9),
    record("satellite_silent", n=10),
    record("satellite_state_indeterminate", n=11),
]
"""Eleven passes: nine the station could have captured, three captured."""


def test_capture_rate_leaves_out_what_the_satellite_decided() -> None:
    assert capture_rate(FLEET) == Proportion(3, 9)


def test_the_miss_rate_counts_only_confirmed_listening_passes() -> None:
    """Passes 1, 2, 4 and 5 were confirmed listening and eligible; 5 missed."""
    assert confirmed_miss_rate(FLEET) == Proportion(1, 4)


def test_completion_counts_any_report_and_execution_any_attempt() -> None:
    """Passes 6 and 9 sent nothing; pass 7 reported that it did not start."""
    assert assignment_completion_rate(FLEET) == Proportion(9, 11)
    assert schedule_execution_rate(FLEET) == Proportion(8, 11)


def test_nothing_to_count_is_not_a_rate() -> None:
    empty = capture_rate([])

    assert (empty.estimate, empty.interval) == (None, None)


def test_the_interval_holds_the_estimate_and_stays_in_bounds() -> None:
    for value in (Proportion(0, 5), Proportion(5, 5), Proportion(3, 9)):
        assert value.interval is not None
        low, high = value.interval
        assert 0.0 <= low <= (value.estimate or 0.0) <= high <= 1.0


def test_a_share_pools_its_seconds() -> None:
    pooled = Share(90.0, 100.0) + Share(10.0, 100.0)

    assert pooled.estimate == pytest.approx(0.5)
    assert Share(0.0, 0.0).estimate is None


def test_delays_are_nearest_rank() -> None:
    measured = delays([float(one) for one in range(1, 21)])

    assert (measured.n, measured.p50_s, measured.p95_s) == (20, 10.0, 19.0)
    assert delays([]).p95_s is None


def test_every_lost_pass_is_a_debit_with_its_reason() -> None:
    budget = loss_budget(FLEET, capture_target=0.9)

    assert budget.eligible == 9
    assert budget.allowed == pytest.approx(0.9)
    assert budget.spent == 6
    assert budget.exhausted
    assert budget.by_reason() == {
        "signal_no_decode": 1,
        "confirmed_miss": 1,
        "station_unavailable": 2,
        "station_not_confirmed_listening": 1,
        "assignment_declined": 1,
    }
    assert [one.reference for one in budget.debits] == [
        "as_4",
        "as_5",
        "as_6",
        "as_7",
        "as_8",
        "as_9",
    ]


def test_only_one_reason_is_a_miss() -> None:
    """Rule 7: the budget is spent by absence, but absence is not a miss."""
    budget = loss_budget(FLEET, capture_target=0.9)
    misses = [one for one in budget.debits if one.reason == "confirmed_miss"]

    assert len(misses) == 1
    assert "satellite_silent" not in DEBIT_REASONS
    assert "successful_reception" not in DEBIT_REASONS


def test_a_budget_within_its_target_has_what_is_left() -> None:
    passes = [record("successful_reception", outcome="decoded", n=n) for n in range(19)]
    passes.append(record("confirmed_miss", n=19))

    budget = loss_budget(passes, capture_target=0.9)

    assert (budget.eligible, budget.spent) == (20, 1)
    assert budget.remaining == pytest.approx(1.0)
    assert budget.remaining_ratio == pytest.approx(0.5)
    assert not budget.exhausted


def test_nothing_eligible_is_no_budget_at_all() -> None:
    budget = loss_budget([record("satellite_silent")], capture_target=0.9)

    assert (budget.eligible, budget.remaining_ratio, budget.exhausted) == (
        0,
        None,
        False,
    )

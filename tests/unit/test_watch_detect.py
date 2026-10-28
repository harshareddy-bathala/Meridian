"""The watch's rules on plain values: a shortfall, persistence, and hysteresis.

The roadmap's tests: one bad pass raises nothing, a step loss raises a warning.
Reference: docs/DECISIONS.md D-294.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.reliability.config import (
    ReliabilityConfigError,
    WatchConfig,
    parse_reliability_config,
)
from meridian.reliability.watch_detect import ReceptionShortfall, compare, judge

T0 = datetime(2026, 10, 12, tzinfo=UTC)
CONFIG = WatchConfig()
BASELINE = {10.0: 6.0, 20.0: 9.0, 30.0: 12.0}


def heard(loss_db: float, *, bins: tuple[float, ...] = (10.0, 20.0, 30.0)) -> dict:
    return {edge: (BASELINE[edge] - loss_db, 8) for edge in bins}


def reception(number: int, loss_db: float) -> ReceptionShortfall:
    found = compare(
        heard(loss_db),
        BASELINE,
        CONFIG,
        assignment_id=f"a-{number}",
        started_at=T0 + timedelta(hours=number),
        end_at=T0 + timedelta(hours=number, minutes=12),
    )
    assert found is not None
    return found


def test_a_shortfall_is_the_median_across_the_bins_crossed() -> None:
    found = compare(
        {10.0: (5.0, 4), 20.0: (4.0, 4), 30.0: (12.0, 4), 50.0: (1.0, 4)},
        BASELINE,
        CONFIG,
        assignment_id="a",
        started_at=T0,
        end_at=T0,
    )

    assert found is not None
    assert found.by_bin == {10.0: 1.0, 20.0: 5.0, 30.0: 0.0}
    assert found.shortfall_db == 1.0


def test_a_reception_crossing_one_bin_is_not_judged() -> None:
    assert (
        compare(
            heard(9.0, bins=(10.0,)),
            BASELINE,
            CONFIG,
            assignment_id="a",
            started_at=T0,
            end_at=T0,
        )
        is None
    )


def test_one_bad_pass_raises_nothing() -> None:
    judged = judge([reception(1, 0.2), reception(2, 9.0), reception(3, 0.4)], CONFIG)

    assert judged is not None
    assert judged.raises is False
    assert judged.shortfall_db == 0.4


def test_a_step_loss_raises_on_its_second_reception() -> None:
    before = [reception(1, 0.1), reception(2, 0.3)]

    first = judge([*before, reception(3, 6.0)], CONFIG)
    second = judge([*before, reception(3, 6.0), reception(4, 6.0)], CONFIG)

    assert first is not None and first.raises is False
    assert second is not None and second.raises is True
    assert second.shortfall_db == 6.0
    assert [one["elevation_deg"] for one in second.affected] == [10.0, 20.0, 30.0]


def test_a_loss_at_one_elevation_alone_does_not_raise() -> None:
    """One bin short is an obstruction's shape, not a chain's."""
    lopsided = [
        compare(
            {10.0: (BASELINE[10.0] - 9.0, 8), 20.0: (9.0, 8), 30.0: (12.0, 8)},
            BASELINE,
            CONFIG,
            assignment_id=f"a-{n}",
            started_at=T0,
            end_at=T0,
        )
        for n in range(3)
    ]

    judged = judge([one for one in lopsided if one is not None], CONFIG)

    assert judged is not None
    assert judged.raises is False
    assert [one["elevation_deg"] for one in judged.affected] == [10.0]


def test_it_clears_below_a_lower_bar_than_it_raises() -> None:
    hovering = judge([reception(n, 2.0) for n in range(3)], CONFIG)
    recovered = judge([reception(n, 1.0) for n in range(3)], CONFIG)

    assert hovering is not None and (hovering.raises, hovering.clears) == (False, False)
    assert recovered is not None and recovered.clears is True


def test_too_few_receptions_is_no_judgement() -> None:
    assert judge([reception(1, 9.0), reception(2, 9.0)], CONFIG) is None


def test_a_clear_bar_at_or_above_the_raise_bar_is_refused() -> None:
    with pytest.raises(ReliabilityConfigError, match="clear_db"):
        parse_reliability_config("[watch]\nraise_db = 2.0\nclear_db = 2.0\n")

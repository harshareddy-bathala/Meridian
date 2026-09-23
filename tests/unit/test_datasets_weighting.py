"""``meridian.datasets.weighting`` — D-153, against numbers worked by hand.

Estimates are built directly, cell counts and all, so each case states the
propensities it weights rather than depending on the binned estimator.

Reference: docs/DECISIONS.md D-152, D-153.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from meridian.datasets.propensity import Candidate, Estimate
from meridian.datasets.weighting import Scored, weigh, weight_of

AT = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
FLOOR = 0.05


def scored(
    taken: int,
    of: int,
    *,
    attempted: bool = True,
    success: bool = True,
    usable: bool | None = None,
) -> Scored:
    """One pass in a cell of ``of`` passes, ``taken`` of them attempted."""
    candidate = Candidate(
        population="own",
        station="st_a",
        satellite_id="norad:57166",
        aos=AT,
        max_elevation_deg=40.0,
        longitude_deg=0.0,
        attempted=attempted,
    )
    return Scored(
        estimate=Estimate(
            candidate=candidate,
            level="station_elevation",
            cell=("own", "st_a", f"cell {taken}/{of}"),
            available=of,
            attempted=taken,
        ),
        usable=attempted if usable is None else usable,
        success=attempted and success,
    )


def test_weights_undo_the_policy_s_preference() -> None:
    """Worked by hand.

    Cell A: 4 available, 2 attempted (p = 0.5) — one success, one failure,
    weight 2 each. Cell B: 4 available, 1 attempted (p = 0.25) — a success,
    weight 4. Unweighted 2/3; Hájek (2 + 4) / 8 = 0.75; ESS 8² / 24 = 8/3.
    """
    passes = [
        scored(2, 4, success=True),
        scored(2, 4, success=False),
        scored(2, 4, attempted=False),
        scored(2, 4, attempted=False),
        scored(1, 4, success=True),
        *[scored(1, 4, attempted=False) for _ in range(3)],
    ]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.unweighted is not None
    assert result.weighted_rate is not None
    assert result.unweighted.estimate == pytest.approx(2 / 3)
    assert result.weighted_rate.estimate == pytest.approx(0.75)
    assert result.ess == pytest.approx(8 / 3)
    assert result.weighted_rate.n == result.ess
    assert (result.available, result.weighted) == (8, 3)
    assert result.unreliable is True


def test_equal_propensities_leave_the_rate_alone() -> None:
    passes = [scored(40, 50, success=n % 4 != 0) for n in range(40)]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.unweighted is not None
    assert result.weighted_rate is not None
    assert result.weighted_rate.estimate == pytest.approx(result.unweighted.estimate)
    assert result.ess == pytest.approx(40)
    assert result.unreliable is False


@pytest.mark.parametrize(("n", "unreliable"), [(29, True), (30, False)])
def test_fewer_than_thirty_effective_passes_is_unreliable(
    n: int, unreliable: bool
) -> None:
    passes = [scored(n, n) for _ in range(n)]

    assert weigh(passes, model="binned-1", floor=FLOOR).unreliable is unreliable


def test_a_tenth_of_n_is_the_bar_for_a_large_sample() -> None:
    """ESS above thirty but under a tenth of n is still unreliable.

    990 weights of 1 and 10 of 100 (propensity 0.01, floor 0.01): Σw = 1990,
    Σw² = 100990, ESS ≈ 39.2 — past 30, short of 100.
    """
    passes = [scored(990, 990) for _ in range(990)] + [
        scored(1, 100) for _ in range(10)
    ]

    result = weigh(passes, model="binned-1", floor=0.01)

    assert result.ess == pytest.approx(1990**2 / 100990)
    assert 30 < result.ess < 100
    assert result.unreliable is True


def test_a_propensity_under_the_floor_is_weighted_at_the_floor() -> None:
    rare = scored(1, 50)

    result = weigh([rare], model="binned-1", floor=FLOOR)

    assert weight_of(rare.estimate, FLOOR) == pytest.approx(20)
    assert result.floored == 1
    assert result.weight_quartiles == pytest.approx((20, 20, 20, 20, 20))


def test_a_pass_nothing_like_which_was_attempted_is_unsupported() -> None:
    orphan = scored(0, 7, attempted=False)

    result = weigh([orphan, scored(1, 1)], model="binned-1", floor=FLOOR)

    assert weight_of(orphan.estimate, FLOOR) is None
    assert result.unsupported == 1
    assert result.weighted == 1


def test_a_cell_where_everything_was_attempted_is_certain() -> None:
    """D-152: a deterministic policy shows up here, and at unsupported."""
    passes = [scored(3, 3) for _ in range(3)] + [
        scored(0, 2, attempted=False) for _ in range(2)
    ]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert (result.certain, result.unsupported) == (3, 2)


def test_an_attempt_with_no_usable_outcome_carries_no_weight() -> None:
    passes = [scored(2, 2, usable=False), scored(2, 2)]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.weighted == 1


def test_nothing_to_weight_says_so_and_is_unreliable() -> None:
    result = weigh([scored(0, 3, attempted=False)], model="binned-1", floor=FLOOR)

    assert result.unweighted is None
    assert result.weighted_rate is None
    assert result.ess == 0.0
    assert result.unreliable is True
    assert result.weight_quartiles == ()


def test_the_wilson_interval_is_the_textbook_one() -> None:
    """50 of 100: 0.4038 to 0.5962, as every table of Wilson intervals gives it."""
    passes = [scored(100, 100, success=n < 50) for n in range(100)]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.unweighted is not None
    assert result.unweighted.low == pytest.approx(0.4038, abs=1e-4)
    assert result.unweighted.high == pytest.approx(0.5962, abs=1e-4)


def test_the_interval_never_leaves_zero_to_one() -> None:
    result = weigh([scored(1, 1)], model="binned-1", floor=FLOOR)

    assert result.unweighted is not None
    assert 0.0 <= result.unweighted.low <= result.unweighted.high <= 1.0


def test_overlap_is_binned_apart_for_attempted_and_not() -> None:
    """Propensity exactly 0.7 lands in the 0.7 bin; 1.0 in the last."""
    passes = [
        scored(7, 10),
        scored(7, 10, attempted=False),
        scored(1, 1),
        scored(0, 5, attempted=False),
    ]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.overlap_attempted == (0, 0, 0, 0, 0, 0, 0, 1, 0, 1)
    assert result.overlap_not_attempted == (1, 0, 0, 0, 0, 0, 0, 1, 0, 0)


def test_weight_quartiles_are_nearest_rank() -> None:
    passes = [scored(1, n) for n in (1, 2, 3, 4, 5)]

    result = weigh(passes, model="binned-1", floor=FLOOR)

    assert result.weight_quartiles == pytest.approx((1, 2, 3, 4, 5))


def test_the_order_passes_arrive_in_changes_nothing() -> None:
    passes = [scored(k, 9, success=k % 2 == 0) for k in range(1, 10)] * 3
    shuffled = random.Random(9).sample(passes, len(passes))

    assert weigh(shuffled, model="binned-1", floor=FLOOR) == weigh(
        passes, model="binned-1", floor=FLOOR
    )


def test_the_diagnostics_render_as_plain_values() -> None:
    parameters = weigh([scored(1, 2)], model="binned-1", floor=FLOOR).parameters()

    assert parameters["model"] == "binned-1"
    assert parameters["weighted_rate"] == {
        "estimate": 1.0,
        "low": pytest.approx(0.2065, abs=1e-4),
        "high": 1.0,
        "n": 1.0,
    }
    assert set(parameters) >= {"ess", "unreliable", "overlap", "floored"}

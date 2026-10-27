"""The baseline's rules on plain values: bins, one vote per reception, thresholds.

Reference: docs/DECISIONS.md D-184, D-291, D-293.
"""

from __future__ import annotations

import pytest

from meridian.reliability.config import (
    BaselineConfig,
    ReliabilityConfigError,
    parse_reliability_config,
)
from meridian.reliability.watch_baseline import (
    Reading,
    bin_of,
    nearest_rank,
    reception_bins,
    train,
)

CONFIG = BaselineConfig()


def test_an_elevation_falls_in_the_bin_below_it() -> None:
    assert [bin_of(e, 10.0) for e in (5.0, 10.0, 19.99, 89.0)] == [0, 10, 10, 80]


def test_quantiles_are_by_nearest_rank() -> None:
    values = [float(v) for v in range(1, 11)]

    assert nearest_rank(values, 0.5) == 5.0
    assert nearest_rank(values, 0.1) == 1.0
    assert nearest_rank([7.0], 0.1) == 7.0
    with pytest.raises(ValueError, match="no values"):
        nearest_rank([], 0.5)


def test_a_reception_is_reduced_to_its_median_in_each_bin() -> None:
    readings = [Reading(12.0, 4.0), Reading(14.0, 9.0), Reading(16.0, 5.0)]

    assert reception_bins(readings, CONFIG) == {10.0: (5.0, 3)}


def test_signal_below_the_floor_is_left_out() -> None:
    readings = [Reading(2.0, 1.0), Reading(4.9, 1.5), Reading(5.0, 3.0)]

    assert reception_bins(readings, CONFIG) == {0.0: (3.0, 1)}


def test_a_long_pass_counts_once() -> None:
    """Forty samples of one pass do not outvote four other passes."""
    long_pass = {10.0: (2.0, 40), 20.0: (4.0, 40)}
    others = [{10.0: (8.0, 4), 20.0: (10.0, 4)} for _ in range(4)]

    (low, high) = train([long_pass, *others], CONFIG)

    assert (low.median_db, low.p10_db, low.receptions, low.samples) == (8.0, 2.0, 5, 56)
    assert high.median_db == 10.0


def test_a_thin_bin_is_left_out() -> None:
    receptions = [{10.0: (8.0, 4), 20.0: (9.0, 4)} for _ in range(5)]
    receptions.append({40.0: (12.0, 4)})

    bins = train(receptions, CONFIG)

    assert [one.elevation_deg for one in bins] == [10.0, 20.0]


def test_too_few_bins_is_no_baseline() -> None:
    receptions = [{10.0: (8.0, 4)} for _ in range(9)]

    assert train(receptions, CONFIG) == []
    assert len(train(receptions, BaselineConfig(min_bins=1))) == 1


def test_a_real_setting_hashes_the_same_however_it_is_spelled() -> None:
    whole = parse_reliability_config("[baseline]\nbin_width_deg = 10\n").baseline
    real = parse_reliability_config("[baseline]\nbin_width_deg = 10.0\n").baseline

    assert whole.sha256() == real.sha256()


@pytest.mark.parametrize(
    "line",
    ["bin_width_deg = 0", "train_s = 60", "min_bins = 0", "min_receptions = 1.5"],
)
def test_a_setting_out_of_range_is_refused(line: str) -> None:
    with pytest.raises(ReliabilityConfigError):
        parse_reliability_config(f"[baseline]\n{line}\n")

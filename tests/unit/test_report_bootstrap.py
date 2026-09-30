"""``meridian.reports.bootstrap`` — station-days resampled whole, and paired.

Reference: docs/DECISIONS.md D-237.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from meridian.reports.bootstrap import cluster_bootstrap, clustered


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def test_items_are_grouped_by_their_key_in_first_seen_order() -> None:
    items = [("a", 1), ("b", 2), ("a", 3)]

    assert clustered(items, lambda one: one[0]) == [[("a", 1), ("a", 3)], [("b", 2)]]


def test_one_seed_gives_one_interval() -> None:
    clusters = [[float(n)] for n in range(20)]

    first = cluster_bootstrap(clusters, {"mean": mean}, seed=7, resamples=300)
    again = cluster_bootstrap(clusters, {"mean": mean}, seed=7, resamples=300)
    other = cluster_bootstrap(clusters, {"mean": mean}, seed=8, resamples=300)

    assert first == again
    assert first != other


def test_a_cluster_is_drawn_whole() -> None:
    """Two clusters of identical values: every draw's mean is one of two numbers
    or their midpoint, never anything a pass-by-pass draw could also give."""
    clusters = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    seen: set[float] = set()

    def record(values: Sequence[float]) -> float:
        seen.add(round(mean(values), 9))
        return mean(values)

    cluster_bootstrap(clusters, {"mean": record}, seed=1, resamples=200)

    assert seen <= {0.0, 0.5, 1.0}


def test_statistics_read_the_same_draws() -> None:
    """Paired: a difference of two statistics on one draw is exactly zero here."""
    clusters = [[float(n)] for n in range(10)]

    drawn = cluster_bootstrap(
        clusters,
        {"first": mean, "second": mean},
        seed=3,
        resamples=100,
    )

    assert drawn["first"] == drawn["second"]


def test_an_undefined_draw_is_dropped_and_counted() -> None:
    clusters = [[0.0], [1.0]]

    def only_mixed(values: Sequence[float]) -> float | None:
        return None if len(set(values)) == 1 else mean(values)

    drawn = cluster_bootstrap(clusters, {"mixed": only_mixed}, seed=2, resamples=400)[
        "mixed"
    ]

    assert 0 < drawn.dropped < 400
    assert drawn.interval is not None


def test_nothing_to_draw_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one cluster"):
        cluster_bootstrap([], {"mean": mean}, seed=1, resamples=10)

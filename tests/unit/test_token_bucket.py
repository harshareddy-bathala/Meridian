"""The token buckets behind D-202's rate limits, on a clock that is a variable."""

from __future__ import annotations

import pytest

from meridian.api.token_bucket import BucketStore, Rate, retry_after_s


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_new_key_starts_full_and_empties() -> None:
    store = BucketStore(Rate(capacity=3, per_second=1), clock=Clock())
    for _ in range(3):
        assert store.wait_s("k") == 0
        store.take("k")

    assert store.wait_s("k") == pytest.approx(1.0)


def test_a_bucket_refills_at_its_rate_and_no_further() -> None:
    clock = Clock()
    store = BucketStore(Rate(capacity=2, per_second=0.5), clock=clock)
    store.take("k")
    store.take("k")

    clock.now += 1
    assert store.wait_s("k") == pytest.approx(1.0)
    clock.now += 1
    assert store.wait_s("k") == 0

    clock.now += 1000
    store.take("k")
    store.take("k")
    assert store.wait_s("k") > 0


def test_keys_do_not_share_a_bucket() -> None:
    store = BucketStore(Rate(capacity=1, per_second=1), clock=Clock())
    store.take("a")

    assert store.wait_s("a") > 0
    assert store.wait_s("b") == 0


def test_a_clock_stepping_backwards_refills_nothing() -> None:
    clock = Clock()
    store = BucketStore(Rate(capacity=1, per_second=1), clock=clock)
    store.take("k")

    clock.now -= 50

    assert store.wait_s("k") == pytest.approx(1.0)


def test_the_store_forgets_the_least_recently_used_key() -> None:
    """Rotated keys cost bounded memory, and evict each other first."""
    store = BucketStore(Rate(capacity=1, per_second=0.001), clock=Clock(), max_keys=3)
    store.take("legitimate")
    for n in range(10):
        store.wait_s(f"rotated-{n}")
        store.wait_s("legitimate")

    assert len(store) == 3
    assert store.wait_s("legitimate") > 0


@pytest.mark.parametrize(("wait_s", "header"), [(0.01, 1), (1.0, 1), (9.2, 10)])
def test_retry_after_is_whole_seconds_and_never_zero(
    wait_s: float, header: int
) -> None:
    assert retry_after_s(wait_s) == header

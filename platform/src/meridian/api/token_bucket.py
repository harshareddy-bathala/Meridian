"""Token buckets by key, bounded in memory, for one process (D-202).

A bucket holds up to ``capacity`` tokens and gains ``per_second`` of them as time
passes. A request takes one; a request that finds none is refused and told how
long until one is there. This module knows nothing about HTTP, MSP or who a key
belongs to — :mod:`meridian.api.rate_limits` decides that — so it can be tested
with a clock that is a variable.

It holds no lock. The API serves each worker's requests on one event loop, and
the only caller checks and takes without an ``await`` in between, so no other
request can run between the two.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["DEFAULT_MAX_KEYS", "BucketStore", "Clock", "Rate", "retry_after_s"]

DEFAULT_MAX_KEYS = 10_000
"""How many keys one store remembers before forgetting the least recently used.

A key costs a few hundred bytes, so the cap is a few megabytes per store. An
attacker rotating tokens or addresses fills it and then only evicts its own
keys, because a legitimate caller is used more recently than a rotated one.
"""

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Rate:
    """How much a bucket holds, and how fast it refills."""

    capacity: float
    per_second: float


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class BucketStore:
    """One bucket per key, all at the same rate."""

    def __init__(
        self,
        rate: Rate,
        *,
        clock: Clock = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        """Create an empty store; a key seen for the first time starts full."""
        self.rate = rate
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def wait_s(self, key: str) -> float:
        """Seconds until ``key`` may take a token: ``0.0`` when it may now."""
        bucket = self._refilled(key)
        if bucket.tokens >= 1:
            return 0.0
        return (1 - bucket.tokens) / self.rate.per_second

    def take(self, key: str) -> None:
        """Take one token from ``key``'s bucket; call only after :meth:`wait_s`."""
        bucket = self._refilled(key)
        bucket.tokens = max(0.0, bucket.tokens - 1)

    def __len__(self) -> int:
        """How many keys are remembered."""
        return len(self._buckets)

    def _refilled(self, key: str) -> _Bucket:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.rate.capacity, updated_at=now)
            self._buckets[key] = bucket
            if len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(key)
            # max(0, …): a clock that stepped backwards refills nothing rather
            # than draining the bucket.
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                self.rate.capacity, bucket.tokens + elapsed * self.rate.per_second
            )
            bucket.updated_at = now
        return bucket


def retry_after_s(wait_s: float) -> int:
    """``wait_s`` as the whole seconds a ``Retry-After`` header carries, at least 1."""
    return max(1, math.ceil(wait_s))

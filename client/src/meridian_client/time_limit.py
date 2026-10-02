"""A bound on how long a block of requests may take, on one clock (D-285).

The station loop is one thread. A heartbeat waiting out a dead route holds it,
and a loop held past a capture edge starts or stops that capture late — the gap
EVALUATION.md §12.1 fails a station for. So the loop gives each tick's requests
a limit, until just past its next edge, and the transport keeps to it: each
attempt's timeouts are cut to what is left, a backoff is cut short, and an
attempt with nothing left is not made.

The limit is given as a duration and turned into an instant here, on this
module's clock, so the caller and the transport never have to share one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

__all__ = ["DeadlineReached", "TimeLimit"]


class DeadlineReached(httpx.TimeoutException):
    """No time was left before the deadline to make another attempt.

    An ``httpx`` timeout, so a caller treats it exactly as it treats a platform
    that could not be reached: the station keeps what it holds and tries again
    on a later tick.
    """


class TimeLimit:
    """The deadline in force, if any. Blocks nest; the earlier deadline wins."""

    def __init__(self) -> None:
        """Start with no deadline."""
        self.deadline: float | None = None

    @contextmanager
    def within(self, seconds: float | None) -> Iterator[None]:
        """Hold every request inside the block to ``seconds`` from now.

        ``None`` adds no bound, and an outer block's still holds.
        """
        previous = self.deadline
        at = None if seconds is None else _monotonic() + max(0.0, seconds)
        if at is not None and (previous is None or at < previous):
            self.deadline = at
        try:
            yield
        finally:
            self.deadline = previous

    def remaining(self) -> float:
        """Seconds left before the deadline; infinite without one."""
        if self.deadline is None:
            return float("inf")
        return max(0.0, self.deadline - _monotonic())

    def attempt_timeout(
        self, read_s: float, connect_s: float, last_error: Exception | None
    ) -> httpx.Timeout:
        """The given timeouts, cut to what is left before the deadline.

        Raises:
            The previous attempt's error, or :class:`DeadlineReached` if there
            was none: no time is left to make this attempt.
        """
        remaining = self.remaining()
        if remaining <= 0.0:
            raise last_error or DeadlineReached("no time left before the deadline")
        return httpx.Timeout(min(read_s, remaining), connect=min(connect_s, remaining))


def _monotonic() -> float:
    """The clock a deadline is on, named so a test can substitute it."""
    return time.monotonic()

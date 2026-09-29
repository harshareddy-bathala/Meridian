"""A source's published rate limits, and the ledger that keeps us under them.

:class:`~meridian_ingest.politeness.RequestBudget` caps one fetch. A source's
own limits are wider than one fetch and outlive it: a free key allowed 5 000
requests in ten minutes, a service allowing 10 000 a day. Two runs of
``meridian-ingest`` an hour apart spend from the same allowance, so the count
has to survive the process — which is what the ledger is.

**We stop before the limit, not at it.** Each window is honoured at
:data:`HEADROOM` of what the source publishes, because the source counts
requests we cannot see — a key shared with a colleague, a retry the server
counted and we did not — and the refusal we are avoiding is theirs, not ours.
A request that would cross the line either waits, when the wait is short, or
stops the fetch and says when to come back (D-223).

**The ledger holds instants and nothing else.** No URL, no key, no identifier:
it is a count, and a file that could leak a key is a file somebody will one
day paste into an issue.

Reference: docs/DECISIONS.md D-134, D-223.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meridian_ingest.politeness import BudgetExhaustedError

__all__ = [
    "HEADROOM",
    "LEDGER_DIRECTORY",
    "RateLimiter",
    "RateWindow",
    "RequestLedger",
]

HEADROOM = 0.9
"""The share of a published limit we allow ourselves."""

LEDGER_DIRECTORY = ".ledger"
"""Beside the sources in the raw root. A source id cannot start with a dot
(:data:`~meridian_ingest.provenance.SOURCE_ID`), so this can never be mistaken
for one."""


@dataclass(frozen=True, slots=True)
class RateWindow:
    """One published limit: so many requests in so many seconds.

    Raises:
        ValueError: Either number is not positive.
    """

    requests: int
    seconds: int

    def __post_init__(self) -> None:
        """Refuse a window that could not admit a request or never resets."""
        if self.requests < 1 or self.seconds < 1:
            message = f"a rate window of {self.requests} in {self.seconds}s is empty"
            raise ValueError(message)

    @property
    def allowed(self) -> int:
        """How many we let ourselves make in one window, under the headroom."""
        return max(1, math.floor(self.requests * HEADROOM))

    def describe(self) -> str:
        """The limit as the source states it, for an operator."""
        return f"{self.requests} per {self.seconds}s"


class RequestLedger:
    """When each request to one source was made, kept on disk between runs.

    Args:
        path: The ledger file. Created, with its directory, on first write.
    """

    def __init__(self, path: Path) -> None:
        """Point at ``path``. Nothing is read yet."""
        self._path = path

    @classmethod
    def for_source(cls, raw_root: Path, source_id: str) -> RequestLedger:
        """The ledger one source keeps under a raw root."""
        return cls(raw_root / LEDGER_DIRECTORY / f"{source_id}.log")

    def instants(self) -> list[datetime]:
        """Every recorded request, oldest first. An absent ledger is empty."""
        if not self._path.is_file():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return sorted(datetime.fromisoformat(line) for line in lines if line.strip())

    def record(self, at: datetime, keep_after: datetime) -> None:
        """Append one request, dropping what no window can still count.

        Args:
            at: When the request is made. Timezone-aware.
            keep_after: Entries at or before this are pruned, so the file stays
                the size of the longest window rather than of the project.
        """
        kept = [one for one in self.instants() if one > keep_after]
        kept.append(at.astimezone(UTC))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{one.isoformat()}\n" for one in kept)
        scratch = self._path.with_suffix(".tmp")
        scratch.write_text(text, encoding="utf-8")
        scratch.replace(self._path)


class RateLimiter:
    """Admits a request only while every window has room for it.

    Args:
        windows: The source's published limits. None admits everything.
        ledger: Where requests are counted between runs.
        max_wait_s: The longest this will sleep for a slot; a longer wait
            stops the fetch instead, for the reason ``Retry-After`` does.
        clock: Injected so a test never waits.
        sleep: Likewise.
    """

    def __init__(
        self,
        windows: tuple[RateWindow, ...],
        ledger: RequestLedger,
        max_wait_s: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Hold the rules. Nothing is read yet."""
        self._windows = windows
        self._ledger = ledger
        self._max_wait_s = max_wait_s
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep or time.sleep

    def remaining(self) -> dict[RateWindow, int]:
        """How many requests each window still admits right now."""
        now = self._clock()
        history = self._ledger.instants()
        return {
            window: window.allowed - _within(history, now, window)
            for window in self._windows
        }

    def acquire(self, what: str) -> None:
        """Account for one request about to be made, waiting if that is brief.

        Args:
            what: The artefact, so a refusal names where the fetch stopped.

        Raises:
            BudgetExhaustedError: A window is full and would not reopen within
                ``max_wait_s``. Raised *instead of* the request.
        """
        if not self._windows:
            return
        wait = self._wait_needed(self._clock())
        if wait > self._max_wait_s:
            message = (
                f"stopping before {what}: the source's published limit would be "
                f"reached, and a slot reopens in {wait:.0f}s. Run it again later"
            )
            raise BudgetExhaustedError(message)
        if wait > 0:
            self._sleep(wait)
        now = self._clock()
        longest = max(window.seconds for window in self._windows)
        self._ledger.record(now, keep_after=now - timedelta(seconds=longest))

    def _wait_needed(self, now: datetime) -> float:
        """Seconds until every window has room for one more request."""
        history = self._ledger.instants()
        waits = [0.0]
        for window in self._windows:
            inside = [one for one in history if one > now - _span(window)]
            if len(inside) >= window.allowed:
                oldest_that_counts = inside[len(inside) - window.allowed]
                reopens = oldest_that_counts + _span(window)
                waits.append((reopens - now).total_seconds())
        return max(waits)


def _span(window: RateWindow) -> timedelta:
    return timedelta(seconds=window.seconds)


def _within(history: list[datetime], now: datetime, window: RateWindow) -> int:
    return sum(1 for one in history if one > now - _span(window))

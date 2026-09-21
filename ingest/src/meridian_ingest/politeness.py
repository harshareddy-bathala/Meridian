"""How many requests we are willing to make, and how long we wait between them.

Every other stage's network traffic goes to our own platform. This one goes to
somebody else's, under terms that often count requests (``access_constraint``
``key_counted``), and an archive has no reason to tolerate a client that
hammers it. So the politeness rules live in their own module, as pure values
and pure functions, and the HTTP binding above them does nothing but obey.

**Written fresh rather than imported from the station client.**
``meridian_client.transport`` has a retry policy, and importing it would make
the ingest distribution depend on the client — inverting a boundary that exists
so a station installs without any of this. The two also answer different
questions: the client's jitter decorrelates *our own fleet* so fifty simulated
stations do not reconverge into a thundering herd, while this one is a single
client trying not to synchronise with *somebody else's* rate window.

**The budget is spent before a request, not counted after one.** A ceiling
checked afterwards is a ceiling already exceeded, and the number an archive
cares about is the number of requests it served.

Reference: docs/DECISIONS.md D-134, D-142.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from meridian_ingest.retrieval import RetrievalError

__all__ = [
    "RETRYABLE_STATUSES",
    "BudgetExhaustedError",
    "RequestBudget",
    "RetryPolicy",
    "is_retryable",
    "parse_retry_after",
]

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Statuses where trying again could plausibly succeed.

Everything else in the 4xx range is our mistake, not a transient one: a 403 is
the same 403 in thirty seconds, and asking again is both useless and rude. A
404 is the strongest case of all — an artefact a listing named and the server
does not have is a fact about the archive that the operator should see, not a
condition to wait out.
"""


class BudgetExhaustedError(RetrievalError):
    """A fetch that stopped because it had spent the requests allowed it.

    Not an error in the sense that something broke. It is the fetch doing what
    it was told, and what an operator does about it — raise the budget, or
    fetch the rest tomorrow — is theirs to decide.
    """


@dataclass(slots=True)
class RequestBudget:
    """A ceiling on how many requests one fetch may make.

    The only mutable value in this distribution, deliberately: a budget that
    could be copied is a budget each retry loop would get its own of, and the
    number would mean nothing.

    Args:
        ceiling: The most requests this fetch may make. Retries included — a
            retry is a request the archive served, whatever we made of the
            response.
        spent: How many have been made. Passed only when resuming.
    """

    ceiling: int
    spent: int = 0

    def __post_init__(self) -> None:
        """Refuse a budget that could not pay for anything."""
        if self.ceiling < 1:
            message = f"a budget of {self.ceiling} requests cannot fetch anything"
            raise ValueError(message)

    @property
    def remaining(self) -> int:
        """How many requests are still allowed."""
        return max(0, self.ceiling - self.spent)

    def spend(self, what: str) -> None:
        """Account for one request about to be made.

        Args:
            what: The artefact it is for, so the refusal names where the fetch
                stopped and an operator can resume from there.

        Raises:
            BudgetExhaustedError: The ceiling has been reached. Raised
                *instead of* the request, which is the whole point: a ceiling
                checked after the fact is a ceiling already exceeded.
        """
        if self.remaining == 0:
            message = (
                f"stopping before {what}: this fetch has spent its {self.ceiling} "
                "requests. Raise the budget or fetch the rest in another run"
            )
            raise BudgetExhaustedError(message)
        self.spent += 1


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter, and a ceiling on waiting.

    Args:
        attempts: How many times one artefact is asked for, first try included.
        base_delay_s: The first delay's ceiling; it doubles from there.
        max_delay_s: The longest this will ever wait, and also the longest
            ``Retry-After`` it will honour rather than refuse.
    """

    attempts: int = 4
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0

    def __post_init__(self) -> None:
        """Refuse a policy that would never try or would wait indefinitely."""
        if self.attempts < 1:
            message = f"{self.attempts} attempts is not a policy, it is a refusal"
            raise ValueError(message)
        if self.base_delay_s < 0 or self.max_delay_s < self.base_delay_s:
            message = (
                f"delays must rise: base {self.base_delay_s}s, max {self.max_delay_s}s"
            )
            raise ValueError(message)

    def delay_before(self, attempt: int) -> float:
        """Seconds to wait before retry ``attempt``, counting from 1.

        Args:
            attempt: Which retry is about to be made.

        Returns:
            A delay in ``[0, min(max_delay_s, base_delay_s * 2**(attempt-1))]``.

        Note:
            **Full jitter**, not a fixed backoff with a small random term. A
            client whose retries land on a fixed schedule keeps hitting the
            same point in an archive's rate window, so the fifth request fails
            for the same reason the first did; spreading the delay over the
            whole interval is what actually breaks that.

            ``random``, not ``secrets``: this schedules a retry, it does not
            generate a credential.
        """
        ceiling = min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1))
        return random.uniform(0.0, ceiling)

    def honour(self, retry_after_s: float, what: str) -> float:
        """A server's own ``Retry-After``, or a refusal if it is too long to wait.

        Args:
            retry_after_s: What the response asked for, in seconds.
            what: The artefact, so a refusal says where the fetch stopped.

        Returns:
            The requested delay, which replaces the computed one outright. A
            server that names a number knows something about its own capacity
            that our backoff curve does not.

        Raises:
            BudgetExhaustedError: It is longer than :attr:`max_delay_s`.

        Note:
            **Waiting an hour inside a fetch is not politeness, it is a hung
            process.** The archive has said come back later; the honest
            response is to stop and let an operator run the command again,
            rather than hold a terminal open and look like a crash.
        """
        if retry_after_s > self.max_delay_s:
            message = (
                f"stopping before {what}: the archive asked for {retry_after_s:.0f}s, "
                f"longer than the {self.max_delay_s:.0f}s this fetch will wait. Run "
                "it again later"
            )
            raise BudgetExhaustedError(message)
        return max(0.0, retry_after_s)


def is_retryable(status: int) -> bool:
    """Whether a status is worth asking again about.

    Args:
        status: The HTTP status returned.

    Returns:
        True for :data:`RETRYABLE_STATUSES`.
    """
    return status in RETRYABLE_STATUSES


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """A ``Retry-After`` header as seconds, whichever of its two forms it takes.

    Args:
        value: The header, or None when the response carried none.
        now: The moment to measure an HTTP-date against, so this stays a pure
            function and a test does not have to wait.

    Returns:
        Seconds to wait, never negative, or None when there is nothing usable
        to read. An unparseable header is None rather than an error: a server
        sending a malformed hint has still told us to slow down, and our own
        backoff is the right answer to fall back on.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # pragma: no cover — an HTTP-date is always GMT
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())

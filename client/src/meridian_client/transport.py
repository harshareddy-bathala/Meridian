"""The station's HTTP transport to the platform.

One place that knows the base URL, the ``MSP-Version`` header, the timeouts and
the retry policy, so that no individual operation reimplements them. Every MSP
call a station makes goes through here.

It sits above ``httpx`` and below the operations. It **owns no protocol
semantics**: it does not know what a heartbeat is, does not interpret an
``error`` code beyond deciding whether retrying could help, and holds no station
state. Deciding what to do about a `401` belongs to the caller (D-024).

**The station never transmits**, and nothing here is an exception: this is an HTTP
client to one host, not a radio.

Reference: docs/MSP-SPEC.md §6, §7, §8; docs/DECISIONS.md D-004, D-024.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from meridian_client.clock import (
    ClockEstimate,
    estimate_clock_offset,
    parse_server_time,
)

__all__ = [
    "MSP_VERSION",
    "MspTransport",
    "ProtocolError",
    "RetryPolicy",
]

MSP_VERSION = "0.1"
"""The version this client speaks, sent on every request (MSP §7).

Sent rather than omitted even though the platform's rule accepts any minor within
a supported major: MSP §7 requires the header, and a client that relies on a
server being lenient is a client that breaks against a stricter one.
"""

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0
"""Bounded so a hung platform cannot stall the station indefinitely.

Ten seconds is generous against a heartbeat that must complete inside D-030's
thirty-second poll interval, and short enough that a station discovers the
platform is unreachable in time to fall back on the work it already holds.
"""


class ProtocolError(Exception):
    """An MSP error response, carrying the stable code the platform returned.

    ``code`` is MSP §6's machine-readable field and the only one a caller may
    branch on. ``message`` is for the station's log and must never be parsed.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        """Build from a parsed MSP error body."""
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter.

    Jitter is not decoration. Phase 3 runs fifty simulated stations, and a fleet
    that all retry on the same doubling schedule after a platform restart
    reconverges into a thundering herd on every subsequent failure — the outage
    then recurs on a fixed period and looks like a platform fault.
    """

    attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def delay_before(self, attempt: int) -> float:
        """Seconds to wait before ``attempt`` (1-based), with full jitter.

        Args:
            attempt: Which retry is about to be made, counting from 1.

        Returns:
            A delay in ``[0, min(max_delay_s, base * 2**(attempt-1))]``. Full
            jitter rather than a fixed backoff plus a small random term, because
            it is the variant that actually decorrelates a fleet.
        """
        ceiling = min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1))
        # `random`, not `secrets`: this schedules a retry, it does not generate a
        # credential. Every token in this project comes from `secrets` instead.
        return random.uniform(0.0, ceiling)


# 429 and 5xx are worth retrying; a 4xx is the station's fault and will fail again
# identically. MSP §6 says a station receiving 401 stops rather than looping — a
# revoked token retried every thirty seconds by fifty stations is a denial of
# service the network inflicts on itself (D-024).
RETRIABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class MspTransport:
    """Sends MSP requests to one platform.

    Args:
        base_url: Platform root, e.g. ``http://localhost:8000``.
        bearer_token: Station credential, or ``None`` before registration. The
            time endpoint is unauthenticated, so a station that has lost its
            token can still construct this and establish a clock offset (MSP §8).
        retry: Backoff policy for transport failures and retriable statuses.
    """

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        """Open a client against ``base_url``."""
        self._retry = retry or RetryPolicy()
        headers = {"MSP-Version": MSP_VERSION}
        if bearer_token is not None:
            headers["Authorization"] = f"Bearer {bearer_token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> MspTransport:
        """Enter a context managing this transport's connections."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close on the way out, whether or not the body raised."""
        self.close()

    def server_time(self) -> ClockEstimate:
        """Query ``GET /msp/v0/time`` and estimate this station's clock offset.

        The station's own send and receive instants are read from its clock
        either side of the request, which is the whole point: the estimate is of
        *this* clock against the platform's, so both ends must come from here.

        Returns:
            The offset and its 1σ uncertainty, in D-025's sign convention.

        Raises:
            ProtocolError: If the platform returned an MSP error body.
            httpx.HTTPError: If the platform could not be reached after the
                retry policy was exhausted.
        """
        sent_at = datetime.now(UTC)
        response = self._get("/msp/v0/time")
        received_at = datetime.now(UTC)

        server_time = parse_server_time(response.json()["server_time"])
        return estimate_clock_offset(sent_at, received_at, server_time)

    def _get(self, path: str) -> httpx.Response:
        """GET ``path``, retrying transport failures and retriable statuses."""
        last_error: Exception | None = None
        for attempt in range(1, self._retry.attempts + 1):
            try:
                response = self._client.get(path)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in RETRIABLE_STATUSES:
                    _raise_for_msp_error(response)
                    return response
                last_error = ProtocolError(
                    response.status_code, "retriable", response.text[:200]
                )
            if attempt < self._retry.attempts:
                _sleep(self._retry.delay_before(attempt))
        raise last_error if last_error else RuntimeError("unreachable")


def _sleep(seconds: float) -> None:
    """Indirection so a test can run the retry path without waiting."""
    time.sleep(seconds)


def _raise_for_msp_error(response: httpx.Response) -> None:
    """Turn an MSP error body into a :class:`ProtocolError`."""
    if response.status_code < httpx.codes.BAD_REQUEST:
        return
    # MSP §6 guarantees the two-field shape, but a proxy or a tunnel can return
    # its own HTML error page — so a body that is not MSP's shape is reported as
    # a server_error rather than crashing on a missing key.
    try:
        body = response.json()
        code = str(body["error"])
        message = str(body["message"])
    except (ValueError, KeyError, TypeError):
        code, message = "server_error", response.text[:200]
    raise ProtocolError(response.status_code, code, message)

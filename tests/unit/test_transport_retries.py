"""The transport's retry loop, run without a network and without waiting.

`tests/unit/test_transport.py` covers the judgements the transport makes as
pure functions — which statuses are retriable, how long to back off, how to read
an error body. This file covers the loop that acts on them: how many requests
actually leave the station, and what is raised when they all fail.

The station's HTTP client is replaced with a stub that returns a scripted
sequence of responses, and ``transport._sleep`` with one that records what it was
asked to wait instead of waiting. Both are the seams the module already provides;
nothing here reaches the network or a clock.
"""

from __future__ import annotations

import httpx
import pytest

from meridian_client import transport as transport_module
from meridian_client.transport import MspTransport, ProtocolError, RetryPolicy


class _ScriptedClient:
    """Returns queued responses in order, and counts how often it was asked."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.methods: list[str] = []

    def request(
        self, method: str, _path: str, *, json: object = None
    ) -> httpx.Response:
        """Return the next scripted response, whatever was asked for.

        Stands in for ``httpx.Client.request``, which is the one call the retry
        loop makes for both reads and writes — so a GET and a POST are retried by
        the same code and this double covers both.
        """
        self.calls += 1
        self.methods.append(method)
        self.sent = json
        return self._responses.pop(0)


def _response(status: int, body: object) -> httpx.Response:
    request = httpx.Request("GET", "http://platform.test/msp/v0/time")
    return httpx.Response(status, json=body, request=request)


@pytest.fixture
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the transport's sleep with a recorder. Returns the delays asked for."""
    delays: list[float] = []
    monkeypatch.setattr(transport_module, "_sleep", delays.append)
    return delays


def _scripted(
    responses: list[httpx.Response],
) -> tuple[MspTransport, _ScriptedClient]:
    """A transport whose HTTP client returns ``responses`` in order."""
    client = _ScriptedClient(responses)
    transport = MspTransport("http://platform.test", retry=RetryPolicy(attempts=3))
    transport._client = client  # type: ignore[assignment]
    return transport, client


def test_a_server_fault_is_retried_until_the_policy_is_exhausted(
    no_waiting: list[float],
) -> None:
    """Three attempts means three requests, then the failure is raised, not swallowed.

    A transport that gave up silently would leave the station believing it had
    reported when it had not — and a heartbeat that is never sent is
    indistinguishable, at the platform, from a station that has stopped.
    """
    transport, client = _scripted([_response(503, {"error": "unavailable"})] * 3)

    with pytest.raises(ProtocolError):
        transport.server_time()

    assert client.calls == 3
    assert len(no_waiting) == 2  # waits between attempts, not after the last


def test_an_exhausted_retry_still_carries_the_platforms_own_error_code(
    no_waiting: list[float],
) -> None:
    """Being throttled and a platform fault are different things to do next.

    MSP §6 makes ``error`` the only field a caller may branch on, and the
    platform sends a real one with every 429 and 5xx. The retry loop used to
    replace it with the literal ``"retriable"`` — a code MSP does not define —
    so a station that exhausted its attempts against a rate limit could not tell
    that apart from a platform that had fallen over, and backing off harder
    versus alerting an operator are opposite responses.
    """
    throttled = {"error": "rate_limited", "message": "Too many requests."}
    transport, client = _scripted([_response(429, throttled)] * 3)

    with pytest.raises(ProtocolError) as excinfo:
        transport.server_time()

    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.status == 429
    assert client.calls == 3
    assert len(no_waiting) == 2


def test_a_fault_that_clears_is_not_reported_as_a_failure(
    no_waiting: list[float],
) -> None:
    """The second attempt succeeds, so the caller sees an estimate and no error.

    This is the ordinary case during a platform restart, and it is the reason the
    retry exists at all: a station must not treat a ten-second outage as a reason
    to stop reporting.
    """
    transport, client = _scripted(
        [
            _response(503, {"error": "unavailable"}),
            _response(200, {"server_time": "2026-08-14T09:31:02Z"}),
        ]
    )

    estimate = transport.server_time()

    assert client.calls == 2
    assert len(no_waiting) == 1  # one wait, between the two attempts
    assert estimate.uncertainty_s > 0.0


def test_a_revoked_token_is_not_retried(no_waiting: list[float]) -> None:
    """D-024: one request, then stop. Tested as behaviour, not as a constant.

    ``test_transport.py`` asserts 401 is absent from ``RETRIABLE_STATUSES``, which
    is a fact about a frozenset. What matters to the network is that the station
    makes exactly one request — fifty stations retrying a revoked token every
    thirty seconds is a denial of service the network inflicts on itself.
    """
    transport, client = _scripted(
        [_response(401, {"error": "unauthorized", "message": "Token revoked."})]
    )

    with pytest.raises(ProtocolError) as excinfo:
        transport.server_time()

    assert excinfo.value.code == "unauthorized"
    assert client.calls == 1
    assert no_waiting == []

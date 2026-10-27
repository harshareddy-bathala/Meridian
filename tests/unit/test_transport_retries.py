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

from meridian_client import time_limit as time_limit_module
from meridian_client import transport as transport_module
from meridian_client.transport import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    DeadlineReached,
    MspTransport,
    ProtocolError,
    RetryPolicy,
)


class _ScriptedClient:
    """Returns queued responses in order, and counts how often it was asked."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.methods: list[str] = []
        self.timeouts: list[httpx.Timeout | None] = []

    def request(
        self,
        method: str,
        _path: str,
        *,
        json: object = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Return the next scripted response, whatever was asked for.

        Stands in for ``httpx.Client.request``, which is the one call the retry
        loop makes for both reads and writes — so a GET and a POST are retried by
        the same code and this double covers both.
        """
        self.calls += 1
        self.methods.append(method)
        self.sent = json
        self.timeouts.append(timeout)
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


# --- a time limit on everything inside a block (D-285) ---------------------------


class _Clock:
    """The transport's monotonic clock, moved by hand and by its own sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(time_limit_module, "_monotonic", lambda: fake.now)
    monkeypatch.setattr(transport_module, "_sleep", fake.sleep)
    return fake


class _TimingClient(_ScriptedClient):
    """Fails every request, and spends the timeout it was given doing it."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__([])
        self.clock = clock

    def request(
        self,
        _method: str,
        _path: str,
        *,
        timeout: httpx.Timeout | None = None,
        **_body: object,
    ) -> httpx.Response:
        self.calls += 1
        self.timeouts.append(timeout)
        assert timeout is not None and timeout.connect is not None
        self.clock.now += timeout.connect
        raise httpx.ConnectTimeout("blackholed")


def _timing(clock: _Clock) -> tuple[MspTransport, _TimingClient]:
    client = _TimingClient(clock)
    transport = MspTransport("http://platform.test", retry=RetryPolicy(attempts=3))
    transport._client = client  # type: ignore[assignment]
    return transport, client


def test_without_a_limit_each_attempt_has_the_clients_own_timeouts(
    clock: _Clock,
) -> None:
    transport, client = _timing(clock)

    with pytest.raises(httpx.ConnectTimeout):
        transport.heartbeat({})

    assert client.calls == 3
    assert all(
        (t.read, t.connect) == (READ_TIMEOUT_S, CONNECT_TIMEOUT_S)
        for t in client.timeouts
        if t is not None
    )


def test_a_limit_is_never_overrun_by_attempts_or_backoff(clock: _Clock) -> None:
    """Three five-second connects would be fifteen seconds; the limit is seven."""
    transport, client = _timing(clock)
    started = clock.now

    with transport.time_limit(7.0), pytest.raises(httpx.TimeoutException):
        transport.heartbeat({})

    assert clock.now - started <= 7.0
    assert client.timeouts[0] is not None and client.timeouts[0].connect == 5.0
    last = client.timeouts[-1]
    assert last is not None and last.connect is not None and last.connect < 5.0


def test_no_time_left_sends_nothing(clock: _Clock) -> None:
    transport, client = _timing(clock)

    with transport.time_limit(0.0), pytest.raises(DeadlineReached):
        transport.heartbeat({})

    assert client.calls == 0


def test_a_nested_limit_takes_the_earlier_and_gives_it_back(clock: _Clock) -> None:
    transport, _ = _timing(clock)

    with transport.time_limit(30.0):
        outer = transport._limit.deadline
        with transport.time_limit(60.0):
            assert transport._limit.deadline == outer
        with transport.time_limit(2.0):
            assert transport._limit.deadline == clock.now + 2.0
        assert transport._limit.deadline == outer
    assert transport._limit.deadline is None

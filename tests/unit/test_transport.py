"""The station transport's decisions, tested without a network.

Covers the three judgements `meridian_client.transport` makes on its own: which
statuses are worth retrying, how long to wait between attempts, and how to read an
error body that may not be MSP's shape at all.

The request itself is not exercised here — that is
`tests/msp_conformance/test_client_platform_agreement.py`, against the real
endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from meridian_client.transport import (
    RETRIABLE_STATUSES,
    ProtocolError,
    RetryPolicy,
    _raise_for_msp_error,
)


def _response(status: int, body: object, *, text: str | None = None) -> httpx.Response:
    request = httpx.Request("GET", "http://platform.test/msp/v0/time")
    if text is not None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=body, request=request)


def test_a_success_raises_nothing() -> None:
    _raise_for_msp_error(_response(200, {"server_time": "2026-08-14T09:31:02.000Z"}))


def test_an_msp_error_body_becomes_a_protocol_error() -> None:
    response = _response(403, {"error": "invalid_invite", "message": "Already used."})

    with pytest.raises(ProtocolError) as excinfo:
        _raise_for_msp_error(response)

    assert excinfo.value.code == "invalid_invite"
    assert excinfo.value.status == 403
    assert excinfo.value.message == "Already used."


def test_a_non_msp_error_body_does_not_crash_the_client() -> None:
    """A tunnel or proxy can return its own HTML page.

    MSP §6 guarantees the two-field shape from the *platform*; nothing guarantees
    it from whatever sits in front of it. A client that raised KeyError here would
    lose the status code it needed to decide whether to retry.
    """
    response = _response(502, None, text="<html><body>Bad Gateway</body></html>")

    with pytest.raises(ProtocolError) as excinfo:
        _raise_for_msp_error(response)

    assert excinfo.value.code == "server_error"
    assert excinfo.value.status == 502


def test_unauthorized_is_not_retriable() -> None:
    """MSP §6 and D-024: a station receiving 401 stops, logs and surfaces it.

    Retrying a revoked token every thirty seconds across fifty simulated stations
    is a denial of service the network inflicts on itself.
    """
    assert 401 not in RETRIABLE_STATUSES


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_client_errors_are_not_retriable(status: int) -> None:
    """They fail again identically; a retry only multiplies the load."""
    assert status not in RETRIABLE_STATUSES


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_rate_limits_and_server_faults_are_retriable(status: int) -> None:
    assert status in RETRIABLE_STATUSES


def test_backoff_is_bounded_by_max_delay() -> None:
    policy = RetryPolicy(attempts=10, base_delay_s=0.5, max_delay_s=8.0)

    for attempt in range(1, 11):
        assert 0.0 <= policy.delay_before(attempt) <= policy.max_delay_s


def test_backoff_ceiling_doubles_until_it_saturates() -> None:
    """Full jitter samples [0, ceiling], so the ceiling is checked by its maximum
    over many draws rather than by one value."""
    policy = RetryPolicy(attempts=6, base_delay_s=1.0, max_delay_s=8.0)

    for attempt, ceiling in ((1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 8.0)):
        worst = max(policy.delay_before(attempt) for _ in range(200))
        assert worst <= ceiling
        assert worst > ceiling / 2  # the draws do reach up towards it


def test_jitter_actually_varies() -> None:
    """Fifty stations retrying on one fixed schedule reconverge into a herd, and
    the outage then recurs on a period that looks like a platform fault."""
    policy = RetryPolicy()

    draws = {policy.delay_before(3) for _ in range(50)}

    assert len(draws) > 1

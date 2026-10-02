"""A time limit holds against a real socket that never answers (D-285).

``test_transport_retries.py`` checks the arithmetic on a hand-moved clock. This
checks that httpx honours the cut timeouts on a real connection: a listener that
completes the handshake and then says nothing, which is what a half-dead route
looks like from the station, holds a request only as long as the limit allows.

Marked as a unit test by living in ``tests/unit``: one loopback socket, no
service.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import httpx
import pytest

from meridian_client.transport import MspTransport, RetryPolicy


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A port whose connections are accepted by the kernel and never answered."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    try:
        yield int(listener.getsockname()[1])
    finally:
        listener.close()


def test_a_silent_platform_holds_a_heartbeat_only_as_long_as_the_limit(
    silent_port: int,
) -> None:
    """Three attempts at a ten-second read would be thirty seconds; this is one."""
    station = MspTransport(
        f"http://127.0.0.1:{silent_port}", retry=RetryPolicy(attempts=3)
    )
    started = time.monotonic()

    with station, station.time_limit(1.0), pytest.raises(httpx.TimeoutException):
        station.heartbeat({"station_id": "st_x"})

    assert time.monotonic() - started < 2.0

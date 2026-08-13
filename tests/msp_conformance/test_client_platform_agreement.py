"""Platform and client agree about the wire, checked from both sides.

`meridian` and `meridian-client` are separate distributions on purpose (D-012):
the client installs on a Pi without `fastapi` or `psycopg`, and neither imports
the other. That boundary is what makes this file necessary. Two independent
implementations of one format drift, and nothing in either package's own tests
would notice — the platform would keep emitting what it always emitted and the
client would keep parsing what it always parsed.

So these tests are the seam: what the platform writes, the client reads, and the
instant survives the trip.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §8; docs/DECISIONS.md D-012, D-025.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from meridian.api import platform_clock
from meridian.api.app import create_app
from meridian.api.platform_clock import format_server_time
from meridian_client.clock import estimate_clock_offset, parse_server_time

INSTANTS = [
    datetime(2026, 8, 14, 9, 31, 2, 123456, tzinfo=UTC),
    datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),  # midnight, no fractional part
    datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),  # rounds within the day
    datetime(2026, 6, 30, 12, 0, 0, 500000, tzinfo=UTC),
]


@pytest.mark.parametrize("instant", INSTANTS)
def test_what_the_platform_writes_the_client_reads(instant: datetime) -> None:
    """The round trip is lossless to the millisecond the platform published.

    Millisecond, not microsecond: `format_server_time` truncates deliberately, so
    the assertion is against the published precision rather than the input's.
    """
    parsed = parse_server_time(format_server_time(instant))

    assert parsed.tzinfo is not None
    assert abs(parsed - instant) < timedelta(milliseconds=1)


@pytest.mark.parametrize("instant", INSTANTS)
def test_the_published_string_ends_in_z(instant: datetime) -> None:
    """MSP §8 shows one form. `+00:00` is the same instant and a different string,
    and a microcontroller comparing suffixes sees the difference."""
    assert format_server_time(instant).endswith("Z")


def test_a_naive_instant_is_refused_rather_than_published() -> None:
    """Publishing a naive datetime as UTC would put a wrong clock on the wire, and
    every station estimating an offset against it would inherit the error."""
    with pytest.raises(ValueError, match="timezone-aware"):
        format_server_time(datetime(2026, 8, 14, 9, 31, 2))  # noqa: DTZ001


def test_the_client_refuses_a_server_time_with_no_offset() -> None:
    """Assuming UTC is how a platform running in a local zone silently shifts
    every station's estimate by that zone."""
    with pytest.raises(ValueError, match="no UTC offset"):
        parse_server_time("2026-08-14T09:31:02")


def test_a_station_with_a_fast_clock_reports_a_negative_offset_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-025's sentence, asserted across the real endpoint rather than in isolation.

    The platform is pinned to a fixed instant and the station's clock is taken to
    read five seconds ahead of it. The offset the station would report is negative
    — which is the whole convention, and the thing a sign error inverts silently.
    """
    platform_now = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)
    monkeypatch.setattr(platform_clock, "utc_now", lambda: platform_now)
    # Port 1: a database nothing can connect to. MSP §8 says `/time` touches no
    # database, and pointing the app at an unreachable one is what holds that to
    # account — if the endpoint ever grew a query, this test would stop passing
    # rather than quietly acquiring a dependency the protocol says it has not got.
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")

    with TestClient(create_app()) as client:
        body = client.get("/msp/v0/time", headers={"MSP-Version": "0.1"}).json()

    station_clock = platform_now + timedelta(seconds=5)
    estimate = estimate_clock_offset(
        sent_at=station_clock,
        received_at=station_clock,
        server_time=parse_server_time(body["server_time"]),
    )

    assert estimate.offset_s == pytest.approx(-5.0)
    # Zero round trip by construction, and still a non-zero uncertainty: D-016
    # says 0.0 is a claim of perfect accuracy, never a small number, so the
    # estimate is floored at the clock's own resolution.
    assert estimate.uncertainty_s > 0.0

"""D-025: the clock offset sign, pinned by a worked example.

There is no code to test yet — the estimator lands with the client at Stage 4.
What this file pins is the *convention*, so that when the estimator is written it
is written against an assertion rather than against whichever sign the author
happened to picture.

    clock_offset = platform clock − station clock

A station whose clock runs fast reports a NEGATIVE offset.

This is worth a test file of its own because a sign error here is invisible.
Nothing crashes, no constraint fires, every number still has plausible
magnitude — and `docs/EVALUATION.md` §6.1's timing-error figure comes out
inverted, in a report, in a viva.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit import require_utc


def estimate_offset(
    sent_at: datetime,
    received_at: datetime,
    server_time: datetime,
) -> float:
    """MSP §4.2's estimator, written once so the convention has a referent.

    ``offset = server_time − (t_send + t_recv) / 2``

    The midpoint of the station's own send and receive instants is its best
    estimate of what its clock read when the platform stamped the response, so
    the difference is the platform's clock minus the station's.

    Moves into ``meridian_client`` at Stage 4.1. It lives here until then because
    the convention needs to be pinned before an implementation can be checked
    against it, not after.
    """
    require_utc(sent_at, "sent_at")
    require_utc(received_at, "received_at")
    require_utc(server_time, "server_time")
    if received_at < sent_at:
        raise ValueError("received_at precedes sent_at")

    midpoint = sent_at + (received_at - sent_at) / 2
    return (server_time - midpoint).total_seconds()


def uncertainty_floor(sent_at: datetime, received_at: datetime) -> float:
    """Half the round trip. Never zero, because the delay is never known."""
    return (received_at - sent_at).total_seconds() / 2


BASE = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)


def test_a_fast_station_clock_reports_a_negative_offset() -> None:
    """The sentence that is the whole decision.

    The station's clock reads 5 s ahead of the platform's. Instantaneous round
    trip, so the midpoint is the station's clock exactly.
    """
    station_now = BASE + timedelta(seconds=5)
    offset = estimate_offset(sent_at=station_now, received_at=station_now, server_time=BASE)
    assert offset == pytest.approx(-5.0)


def test_a_slow_station_clock_reports_a_positive_offset() -> None:
    station_now = BASE - timedelta(seconds=5)
    offset = estimate_offset(sent_at=station_now, received_at=station_now, server_time=BASE)
    assert offset == pytest.approx(+5.0)


def test_the_round_trip_is_split_at_its_midpoint() -> None:
    """A 400 ms round trip on a perfectly synchronised clock reads as zero offset.

    Taking `server_time − t_send` instead — the obvious wrong implementation —
    would report +200 ms of skew for a station with no skew at all, and would do
    it consistently, so it would look like a real measurement.
    """
    sent = BASE
    received = BASE + timedelta(milliseconds=400)
    server_time = BASE + timedelta(milliseconds=200)

    assert estimate_offset(sent, received, server_time) == pytest.approx(0.0)


def test_uncertainty_is_never_zero_when_the_round_trip_is_not() -> None:
    """MSP §4.2: null means unknown; 0.0 is a claim.

    Stage 4.1 requires `uncertainty >= RTT / 2`, because the platform's stamp
    could have been taken at either end of the round trip.
    """
    sent = BASE
    received = BASE + timedelta(milliseconds=400)
    assert uncertainty_floor(sent, received) == pytest.approx(0.2)
    assert uncertainty_floor(sent, received) > 0.0


def test_naive_timestamps_are_refused() -> None:
    """All timestamps UTC, no exceptions — enforced, not assumed."""
    naive = datetime(2026, 8, 14, 9, 31, 2)  # noqa: DTZ001
    with pytest.raises(ValueError, match="naive"):
        estimate_offset(naive, naive, BASE)

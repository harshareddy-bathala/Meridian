"""D-025: the clock offset sign, pinned by a worked example.

    clock_offset = platform clock − station clock

A station whose clock runs fast reports a NEGATIVE offset.

This is worth a test file of its own because a sign error here is invisible.
Nothing crashes, no constraint fires, every number still has plausible
magnitude — and `docs/EVALUATION.md` §6.1's timing-error figure comes out
inverted, in a report, in a viva.

**These assertions predate the code they now check.** The file was written at
Stage 0 against a local copy of the estimator, so that the convention was fixed
before an implementation could be written to whichever sign its author happened to
picture. At Stage 4.1 the estimator moved into `meridian_client.clock`, exactly as
the original file said it would, and the local copy was deleted — so what runs here
is the real one. The assertions are unchanged, which is the point: they were the
specification, and now they are the test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian_client.clock import (
    CLOCK_RESOLUTION_S,
    estimate_clock_offset,
    require_utc,
)


def estimate_offset(
    sent_at: datetime,
    received_at: datetime,
    server_time: datetime,
) -> float:
    """The offset alone, so the assertions below read as they always did."""
    return estimate_clock_offset(sent_at, received_at, server_time).offset_s


def uncertainty_floor(sent_at: datetime, received_at: datetime) -> float:
    """Half the round trip. Never zero, because the delay is never known."""
    server_time = sent_at  # irrelevant to the uncertainty, which is RTT-derived
    return estimate_clock_offset(sent_at, received_at, server_time).uncertainty_s


BASE = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)


def test_a_fast_station_clock_reports_a_negative_offset() -> None:
    """The sentence that is the whole decision.

    The station's clock reads 5 s ahead of the platform's. Instantaneous round
    trip, so the midpoint is the station's clock exactly.
    """
    station_now = BASE + timedelta(seconds=5)
    offset = estimate_offset(
        sent_at=station_now, received_at=station_now, server_time=BASE
    )
    assert offset == pytest.approx(-5.0)


def test_a_slow_station_clock_reports_a_positive_offset() -> None:
    station_now = BASE - timedelta(seconds=5)
    offset = estimate_offset(
        sent_at=station_now, received_at=station_now, server_time=BASE
    )
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


def test_uncertainty_is_never_zero_when_the_round_trip_measures_zero() -> None:
    """The case that a coarse clock actually produces, and D-016 forbids.

    Windows resolves `datetime.now()` to about 15.6 ms, so a localhost round trip
    completes inside one tick and `received_at - sent_at` is *exactly* zero. Half
    of that is 0.0, and 0.0 is not a small uncertainty — it is a station claiming
    it knows its clock perfectly, which is the one thing MSP §4.2 says the field
    must never say. `docs/EVALUATION.md` §6.1 discards timing error smaller than
    the reported uncertainty, so a zero here silently keeps every error it should
    have thrown away.

    Found by running the real client against the real endpoint on localhost, not
    by reading the code.
    """
    assert uncertainty_floor(BASE, BASE) > 0.0
    assert uncertainty_floor(BASE, BASE) == pytest.approx(CLOCK_RESOLUTION_S)


def test_the_uncertainty_floor_does_not_inflate_a_real_round_trip() -> None:
    """The floor is a floor, not a pad. A 400 ms round trip on any clock this
    project runs on is far above the instrument bound, and must be reported as
    measured rather than widened."""
    received = BASE + timedelta(milliseconds=400)

    assert uncertainty_floor(BASE, received) == pytest.approx(0.2)
    assert CLOCK_RESOLUTION_S < 0.2  # the premise of the assertion above


def test_naive_timestamps_are_refused() -> None:
    """All timestamps UTC, no exceptions — enforced, not assumed."""
    naive = datetime(2026, 8, 14, 9, 31, 2)  # noqa: DTZ001
    with pytest.raises(ValueError, match="naive"):
        estimate_offset(naive, naive, BASE)


def test_an_aware_but_non_utc_timestamp_is_refused() -> None:
    """Aware is not the same as UTC, and this station sits at +05:30.

    The naive case above is the obvious one. This is the one that would actually
    happen here: a local-time timestamp carrying its offset passes every "is it
    timezone-aware" check and is still five and a half hours from what the
    estimator assumes it is being handed.

    `meridian.orbit.require_utc` has a twin of this assertion in
    `tests/unit/test_layout.py`. The two copies are deliberate — `meridian-client`
    depends on `httpx` and nothing else so the database boundary holds at install
    time (D-012) — and the duplicate is only worth having if both halves are held
    to the same rule.
    """
    india_standard_time = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 8, 14, 15, 1, 2, tzinfo=india_standard_time)

    with pytest.raises(ValueError, match="not UTC"):
        require_utc(local, "sent_at")


def test_a_clock_that_stepped_mid_request_is_refused() -> None:
    """Receiving before sending is not a small error, it is a different clock.

    NTP can step a station's clock backwards while a request is in flight, and
    the midpoint of a negative interval is not an estimate of anything — the
    offset it produces is wrong by however far the clock jumped, with nothing in
    the number to show it. Refused rather than reported, because
    `docs/EVALUATION.md` §6.1 keeps any timing error larger than the reported
    uncertainty, and this one would arrive with a plausible small uncertainty
    attached.
    """
    sent = BASE
    received = BASE - timedelta(seconds=2)

    with pytest.raises(ValueError, match="precedes"):
        estimate_offset(sent, received, BASE)

"""Deriving a station's liveness from the age of its last heartbeat.

``meridian.registry.liveness`` is pure: it reads no clock and touches no
database, so every case here is stated as an age rather than waited for. The
thresholds are written out as literal seconds rather than imported from the
module under test — a test that computes its expectation from the constant it is
checking passes when both are wrong together, and these two numbers come from
SC-5 rather than from the code.

Reference: docs/DECISIONS.md D-013, D-054; docs/PROJECT.md §9 SC-5.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.registry.liveness import (
    MAX_HEARTBEAT_INTERVAL_S,
    derive_liveness,
    is_heartbeat_interval_consistent,
)

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)

# SC-5: stale at 60 s, offline at 90 s.
STALE_AFTER_S = 60
OFFLINE_AFTER_S = 90


def at_age(age_s: float) -> datetime:
    """A heartbeat instant ``age_s`` seconds before :data:`NOW`."""
    return NOW - timedelta(seconds=age_s)


def test_a_station_that_never_reported_is_never_seen() -> None:
    """Distinct from `offline`, and the distinction is operational.

    A station that registered and never heartbeat is a commissioning problem —
    wrong token, wrong URL, never started. One that reported and stopped is a
    fault in something that was working. Collapsing them would send an operator
    looking in the wrong place.
    """
    assert derive_liveness(None, now=NOW) == "never_seen"


@pytest.mark.parametrize("age_s", [0, 1, 29, 30, 59, 59.9])
def test_a_recent_heartbeat_is_online(age_s: float) -> None:
    assert derive_liveness(at_age(age_s), now=NOW) == "online"


@pytest.mark.parametrize("age_s", [60, 61, 89, 89.9])
def test_two_missed_heartbeats_is_stale(age_s: float) -> None:
    assert derive_liveness(at_age(age_s), now=NOW) == "stale"


@pytest.mark.parametrize("age_s", [90, 91, 600, 86_400])
def test_three_missed_heartbeats_is_offline(age_s: float) -> None:
    """SC-5 requires an injected node failure to be detected within 90 s.

    The boundary is inclusive, so a station exactly 90 s silent is already
    offline — detection *within* 90 s is what the criterion asks for, and
    reporting at 90.001 s would miss it by the width of the comparison.
    """
    assert derive_liveness(at_age(age_s), now=NOW) == "offline"


def test_the_boundaries_fall_on_the_thresholds_themselves() -> None:
    """Stated once, explicitly, because an off-by-one here is invisible.

    Every value either side of both thresholds is covered by the parametrized
    cases above; this asserts the two exact instants, which is where a `>` that
    should have been `>=` would hide.
    """
    assert derive_liveness(at_age(STALE_AFTER_S - 0.001), now=NOW) == "online"
    assert derive_liveness(at_age(STALE_AFTER_S), now=NOW) == "stale"
    assert derive_liveness(at_age(OFFLINE_AFTER_S - 0.001), now=NOW) == "stale"
    assert derive_liveness(at_age(OFFLINE_AFTER_S), now=NOW) == "offline"


def test_a_heartbeat_stamped_in_the_future_is_online() -> None:
    """A fast station clock is not a dead station.

    MSP §8 publishes platform time precisely because station clocks are not
    trusted. Treating skew as a fault would report the one condition the clock
    endpoint exists to let a station fix.
    """
    assert derive_liveness(NOW + timedelta(seconds=45), now=NOW) == "online"


def test_a_naive_now_is_refused() -> None:
    """CLAUDE.local.md §6: a naive datetime anywhere is a bug.

    Subtracted as though it were UTC, it would misreport a station in another
    timezone by whole hours — silently, and in the direction that looks like a
    dead network.
    """
    with pytest.raises(ValueError, match="timezone-aware"):
        derive_liveness(at_age(10), now=datetime(2026, 8, 8, 12, 0, 0))  # noqa: DTZ001


def test_a_naive_heartbeat_instant_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        derive_liveness(datetime(2026, 8, 8, 11, 59, 0), now=NOW)  # noqa: DTZ001


@pytest.mark.parametrize("interval_s", [1, 15, 30])
def test_an_interval_the_thresholds_still_describe_is_accepted(interval_s: int) -> None:
    """At D-030's 30 s, two intervals fit inside the 60 s stale threshold."""
    assert is_heartbeat_interval_consistent(interval_s) is True


@pytest.mark.parametrize("interval_s", [31, 45, 60, 120])
def test_an_interval_that_would_make_a_healthy_station_flap_is_refused(
    interval_s: int,
) -> None:
    """Past 30 s the arithmetic inverts.

    At 45 s a station heartbeating exactly on time is more than 60 s past its
    last one for a third of every cycle, so it flaps into `stale` while
    behaving exactly as specified — and every reliability figure reading
    liveness inherits that.
    """
    assert is_heartbeat_interval_consistent(interval_s) is False


@pytest.mark.parametrize("interval_s", [0, -1])
def test_a_nonsense_interval_is_refused(interval_s: int) -> None:
    """Zero would mean continuous heartbeats; negative means nothing at all."""
    assert is_heartbeat_interval_consistent(interval_s) is False


def test_the_largest_usable_interval_is_the_documented_thirty() -> None:
    """D-030 fixes 30 s polling for all of MSP 0.x, and it is the ceiling."""
    assert MAX_HEARTBEAT_INTERVAL_S == 30

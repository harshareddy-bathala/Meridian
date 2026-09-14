"""D-093: what a pass may say about where its station is, as a table.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian.api.public.window_privacy import (
    ceil_minute,
    floor_minute,
    publish_angle,
    publish_window,
)

AT = datetime(2026, 9, 14, 6, 41, 27, 380_000, tzinfo=UTC)


def test_a_window_is_widened_to_the_minutes_around_it() -> None:
    """Start floored, end ceiled: the published window contains the real one."""
    window = publish_window(AT, AT + timedelta(minutes=11, seconds=5))

    assert window.start == datetime(2026, 9, 14, 6, 41, tzinfo=UTC)
    assert window.end == datetime(2026, 9, 14, 6, 53, tzinfo=UTC)
    assert window.start <= AT
    assert window.end >= AT + timedelta(minutes=11, seconds=5)


def test_an_instant_already_on_the_minute_is_not_moved() -> None:
    on_the_minute = datetime(2026, 9, 14, 6, 41, tzinfo=UTC)

    assert floor_minute(on_the_minute) == on_the_minute
    assert ceil_minute(on_the_minute) == on_the_minute


def test_a_window_in_another_offset_is_published_in_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))

    assert floor_minute(AT.astimezone(ist)) == datetime(2026, 9, 14, 6, 41, tzinfo=UTC)


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        floor_minute(datetime(2026, 9, 14, 6, 41))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("degrees", "published"),
    [(72.4, 72), (72.6, 73), (0.2, 0), (359.6, 360), (-0.4, 0)],
)
def test_angles_are_whole_degrees(degrees: float, published: int) -> None:
    assert publish_angle(degrees) == published

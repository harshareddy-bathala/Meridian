"""``bracket_refinement`` against curves whose answers are known in closed form.

The point of separating the refinement from the propagator is that it can be
checked without one. Every expected value below comes from solving the curve by
hand — a line, a parabola, a sine — never from running the function under test,
so a search that converged confidently to the wrong instant would fail here
rather than agree with itself.

Marked as a unit test by living in ``tests/unit``: no database, no network and
no element set.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.bracket_refinement import refine_maximum, refine_zero_crossing

T0 = datetime(2026, 1, 1, tzinfo=UTC)
"""An arbitrary anchor. Every curve below is written in seconds from here."""

TOLERANCE_S = 0.001
"""Far tighter than the propagator ever needs, so a test failure means the
search is wrong rather than that it stopped early."""

SINE_PERIOD_S = 600.0
"""Ten minutes — the length of a low Earth orbit pass, so the sine cases are
shaped roughly like the elevation curve this code exists to search."""


def at_seconds(offset_s: float) -> datetime:
    """The instant ``offset_s`` after the anchor."""
    return T0 + timedelta(seconds=offset_s)


def seconds_at(t: datetime) -> float:
    """Seconds from the anchor to ``t`` — the inverse of :func:`at_seconds`."""
    return (t - T0).total_seconds()


def rising_line_through_ten(t: datetime) -> float:
    """``s - 10``: negative before the tenth second, positive after it."""
    return seconds_at(t) - 10.0


def falling_line_through_ten(t: datetime) -> float:
    """``10 - s``: the same root, approached from the other direction."""
    return 10.0 - seconds_at(t)


def sine_over_one_pass(t: datetime) -> float:
    """One period of a sine: up through zero at 0 s, down through it at 300 s."""
    return math.sin(2 * math.pi * seconds_at(t) / SINE_PERIOD_S)


def parabola_peaking_at_seven(t: datetime) -> float:
    """``-(s - 7)^2``: a single maximum at the seventh second."""
    return -((seconds_at(t) - 7.0) ** 2)


# --- crossings ----------------------------------------------------------------


def test_a_rising_crossing_is_found_where_the_line_says_it_is() -> None:
    found = refine_zero_crossing(
        rising_line_through_ten, at_seconds(0), at_seconds(30), TOLERANCE_S
    )
    assert seconds_at(found) == pytest.approx(10.0, abs=TOLERANCE_S)


def test_a_falling_crossing_is_found_with_the_bracket_running_backwards() -> None:
    """Loss of signal brackets run later-to-earlier, and must work the same.

    ``t_below`` is here the *later* instant. If the bisection assumed the two
    arrived in time order it would walk away from the root instead of toward it.
    """
    found = refine_zero_crossing(
        falling_line_through_ten, at_seconds(30), at_seconds(0), TOLERANCE_S
    )
    assert seconds_at(found) == pytest.approx(10.0, abs=TOLERANCE_S)


def test_the_instant_returned_is_on_the_non_negative_side() -> None:
    """A pass must not be reported as starting while still below the floor.

    Documented behaviour, and load-bearing: the span between the acquisition
    and the loss this returns has to be time spent above the horizon, so both
    boundaries land on the side where the curve is non-negative.
    """
    rising = refine_zero_crossing(
        rising_line_through_ten, at_seconds(0), at_seconds(30), TOLERANCE_S
    )
    falling = refine_zero_crossing(
        falling_line_through_ten, at_seconds(30), at_seconds(0), TOLERANCE_S
    )

    assert rising_line_through_ten(rising) >= 0
    assert falling_line_through_ten(falling) >= 0


def test_it_finds_the_root_inside_the_bracket_not_another_one() -> None:
    """A sine has a root every half period; the bracket chooses which.

    Bracketed around the descending crossing at 300 s, with roots at 0 s and
    600 s also on the curve.
    """
    found = refine_zero_crossing(
        sine_over_one_pass, at_seconds(400), at_seconds(200), TOLERANCE_S
    )
    assert seconds_at(found) == pytest.approx(300.0, abs=TOLERANCE_S)


def test_a_tighter_tolerance_gives_a_closer_answer() -> None:
    """The tolerance is honoured rather than being a number that goes unused.

    The bracket is deliberately lopsided about the root at 300 s. A symmetric
    one puts the very first midpoint exactly on the answer, and then every
    tolerance produces the same instant and this test proves nothing.
    """
    coarse = refine_zero_crossing(
        sine_over_one_pass, at_seconds(500), at_seconds(200), 10.0
    )
    fine = refine_zero_crossing(
        sine_over_one_pass, at_seconds(500), at_seconds(200), 0.01
    )

    assert abs(seconds_at(coarse) - 300.0) <= 10.0
    assert abs(seconds_at(fine) - 300.0) <= 0.01
    assert abs(seconds_at(fine) - 300.0) < abs(seconds_at(coarse) - 300.0)


def test_a_non_positive_tolerance_is_refused_rather_than_looping() -> None:
    """`timedelta` holds whole microseconds, so the midpoint eventually stops
    moving and the loop would never exit on its own."""
    with pytest.raises(ValueError, match="tolerance_s"):
        refine_zero_crossing(
            rising_line_through_ten, at_seconds(0), at_seconds(30), 0.0
        )


# --- maxima -------------------------------------------------------------------


def test_the_peak_of_a_parabola_is_found_where_calculus_says_it_is() -> None:
    found = refine_maximum(
        parabola_peaking_at_seven, at_seconds(0), at_seconds(30), TOLERANCE_S
    )
    assert seconds_at(found) == pytest.approx(7.0, abs=TOLERANCE_S)


def test_the_peak_of_a_sine_is_a_quarter_period_in() -> None:
    """Culmination on a pass-shaped curve, at 150 s of a 600 s period."""
    found = refine_maximum(
        sine_over_one_pass, at_seconds(0), at_seconds(SINE_PERIOD_S / 2), TOLERANCE_S
    )
    assert seconds_at(found) == pytest.approx(SINE_PERIOD_S / 4, abs=TOLERANCE_S)


def test_the_peak_is_not_the_nearest_coarse_sample() -> None:
    """The whole reason this refinement exists.

    Seven seconds is not a multiple of the thirty-second coarse step a real
    scan would use, so a search that returned its best sample instead of
    refining would answer 0 s or 30 s. Both are further out than the assertion
    in :func:`test_the_peak_of_a_parabola_is_found_where_calculus_says_it_is`
    allows, and this states that plainly rather than leaving it implied.
    """
    found = refine_maximum(
        parabola_peaking_at_seven, at_seconds(0), at_seconds(30), TOLERANCE_S
    )
    assert abs(seconds_at(found) - 7.0) < 0.5


def test_a_curve_still_rising_at_the_end_peaks_at_the_end() -> None:
    """A pass cut short by the scan window: the maximum is the boundary."""
    found = refine_maximum(seconds_at, at_seconds(0), at_seconds(30), TOLERANCE_S)
    assert seconds_at(found) == pytest.approx(30.0, abs=TOLERANCE_S)


def test_a_flat_curve_terminates_instead_of_hanging() -> None:
    """Every comparison ties, so the bracket must shrink on the arithmetic alone.

    The instant is meaningless here and is not asserted; that the call returns
    at all, somewhere inside the bracket, is the property under test.
    """
    found = refine_maximum(lambda _: 5.0, at_seconds(0), at_seconds(30), TOLERANCE_S)
    assert 0.0 <= seconds_at(found) <= 30.0


def test_a_backwards_bracket_is_refused() -> None:
    """Unlike a crossing bracket, a maximum bracket has a required direction."""
    with pytest.raises(ValueError, match="precedes"):
        refine_maximum(
            parabola_peaking_at_seven, at_seconds(30), at_seconds(0), TOLERANCE_S
        )


def test_a_non_positive_tolerance_is_refused_for_a_maximum_too() -> None:
    with pytest.raises(ValueError, match="tolerance_s"):
        refine_maximum(parabola_peaking_at_seven, at_seconds(0), at_seconds(30), 0.0)

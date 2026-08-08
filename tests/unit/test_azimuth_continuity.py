"""Azimuth unwrapping — the rule that stops a rotator taking the long way round.

Pure arithmetic with no propagator involved, so every case is stated outright.
Expected values are written as literals rather than computed with the function
under test, which would pass whenever both were wrong together.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 6, "azimuth continuity".
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from meridian.orbit.azimuth_continuity import AMBIGUOUS_STEP_DEG, unwrap_azimuth_deg


def test_an_empty_series_is_empty() -> None:
    assert unwrap_azimuth_deg([]) == []


def test_a_single_sample_is_returned_unchanged() -> None:
    """Anchored to the compass reading, not normalised.

    A reader checking one sample against a published prediction should see the
    published number, not an equivalent one.
    """
    assert unwrap_azimuth_deg([359.5]) == [359.5]


def test_a_series_that_never_crosses_north_is_untouched() -> None:
    assert unwrap_azimuth_deg([10.0, 20.0, 35.0, 50.0]) == [10.0, 20.0, 35.0, 50.0]


def test_crossing_north_northward_continues_past_360() -> None:
    """The case the module exists for.

    Raw compass output would be [358, 359, 0, 1]: a rotator reading that sees
    the antenna jump back 359 degrees and slews almost a full turn, during a
    pass that will not come again for ninety minutes.
    """
    assert unwrap_azimuth_deg([358.0, 359.0, 0.0, 1.0]) == [358.0, 359.0, 360.0, 361.0]


def test_crossing_north_southward_continues_below_zero() -> None:
    """The same wrap in the other direction — a satellite tracking east to west."""
    assert unwrap_azimuth_deg([2.0, 1.0, 0.0, 359.0]) == [2.0, 1.0, 0.0, -1.0]


def test_repeated_crossings_keep_accumulating() -> None:
    """Two full turns are two full turns.

    A series that revolves twice must not fold back into 0-360: the accumulated
    value is what tells a rotator with cable limits that it needs to unwind.

    The series has to genuinely revolve for this to mean anything. An
    alternating 350, 10, 350, 10 does *not* — each step is twenty degrees the
    short way and the antenna is oscillating across north, not going round — so
    it stays near 360 and is correct to do so.
    """
    revolving = [0.0, 90.0, 180.0, 270.0, 0.0, 90.0, 180.0, 270.0, 0.0]

    assert unwrap_azimuth_deg(revolving) == [
        0.0,
        90.0,
        180.0,
        270.0,
        360.0,
        450.0,
        540.0,
        630.0,
        720.0,
    ]


def test_oscillating_across_north_does_not_accumulate() -> None:
    """The counterpart, so the rule above is not mistaken for "always increase"."""
    assert unwrap_azimuth_deg([350.0, 10.0, 350.0, 10.0]) == [
        350.0,
        370.0,
        350.0,
        370.0,
    ]


def test_every_step_is_the_shortest_way_round() -> None:
    """The invariant, checked rather than illustrated.

    Any sequence of samples close enough together must produce steps under half
    a turn. This holds for the cases above by inspection; here it is asserted
    across a full revolution so a future change cannot satisfy the examples
    while breaking the rule.
    """
    every_ten_degrees = [float(deg % 360) for deg in range(0, 720, 10)]

    unwrapped = unwrap_azimuth_deg(every_ten_degrees)
    steps = [later - earlier for earlier, later in pairwise(unwrapped)]

    assert all(abs(step) < AMBIGUOUS_STEP_DEG for step in steps)
    assert steps == pytest.approx([10.0] * len(steps))


def test_a_zenith_pass_swinging_almost_half_a_turn_still_unwraps() -> None:
    """Near zenith azimuth swings fastest, and this is where it nearly breaks.

    A 179-degree step is still unambiguous and must be followed. The sample
    after it is the interesting one: it has to continue from the unwrapped
    value, not from the compass reading.
    """
    assert unwrap_azimuth_deg([10.0, 189.0, 200.0]) == [10.0, 189.0, 200.0]
    assert unwrap_azimuth_deg([350.0, 169.0, 180.0]) == [350.0, 169.0 + 360.0, 540.0]


def test_exactly_half_a_turn_resolves_negative_and_is_documented_as_ambiguous() -> None:
    """Pinning behaviour that has no right answer, so a change is deliberate.

    Half a turn is equally far both ways and no unwrapping can recover which way
    the antenna went. The function does not raise — a caller plotting a ground
    track is unharmed and only a caller driving a rotator is — so what it does
    instead is fixed here rather than left to the modulo operator's convention.
    """
    assert AMBIGUOUS_STEP_DEG == 180.0
    assert unwrap_azimuth_deg([0.0, 180.0]) == [0.0, -180.0]

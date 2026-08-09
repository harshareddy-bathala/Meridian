"""``find_elevation_passes`` against elevation curves solved by hand.

The search takes a curve as a callable, so every case here is an elevation
function whose horizon crossings and culmination are known in closed form —
no element set, no propagator, no database. An expected value computed with the
code under test would agree with itself whatever both did; these come from
``arcsin`` and from arithmetic written out in the docstrings.

Marked as a unit test by living in ``tests/unit``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.pass_search import ElevationScan, find_elevation_passes

T0 = datetime(2026, 1, 1, tzinfo=UTC)
"""An arbitrary anchor. Every curve below is written in seconds from here."""

AMPLITUDE_DEG = 60.0
PERIOD_S = 1200.0
"""A sine of this period is above zero for its first 600 s — about the length of
a real low Earth orbit pass, so the geometry the search sees is the right shape."""

COARSE_STEP_S = 35.0
"""Deliberately not a divisor of 300 s or 600 s, so neither the culmination nor
either boundary can land on a coarse sample and pass by luck."""

TIME_TOLERANCE_S = 0.1
"""Twice the module's own 0.05 s refinement tolerance."""

CULMINATION_TOLERANCE_S = 1.0
"""Looser than a boundary: elevation is flat at a peak, so the last few ternary
comparisons are decided by floating-point noise rather than by the curve."""


def at_seconds(offset_s: float) -> datetime:
    """The instant ``offset_s`` after the anchor."""
    return T0 + timedelta(seconds=offset_s)


def seconds_at(t: datetime) -> float:
    """Seconds from the anchor to ``t``."""
    return (t - T0).total_seconds()


def sine_elevation_deg(t: datetime) -> float:
    """``60 sin(2 pi s / 1200)``: rises through zero at 0 s, sets at 600 s."""
    return AMPLITUDE_DEG * math.sin(2 * math.pi * seconds_at(t) / PERIOD_S)


def one_short_pass_deg(t: datetime) -> float:
    """``40 (1 - ((s - 260)/60)^2)``: above zero only between 200 s and 320 s.

    A single 120-second pass, continuous everywhere and negative outside it.
    Short enough that a coarse step of 600 s steps clean over it.
    """
    return 40.0 * (1.0 - ((seconds_at(t) - 260.0) / 60.0) ** 2)


def grazing_pass_deg(t: datetime) -> float:
    """``-40 ((s - 260)/60)^2``: touches zero at 260 s, below it everywhere else.

    A satellite that reaches the floor exactly and climbs no further.
    """
    return -40.0 * ((seconds_at(t) - 260.0) / 60.0) ** 2


def scan_of(start_s: float, end_s: float, **kwargs: float) -> ElevationScan:
    """An :class:`ElevationScan` over seconds from the anchor."""
    return ElevationScan(at_seconds(start_s), at_seconds(end_s), **kwargs)


def test_one_pass_is_found_where_the_sine_crosses_zero() -> None:
    """AOS at 0 s and LOS at 600 s, the two roots inside the scan."""
    found = find_elevation_passes(
        sine_elevation_deg, scan_of(-300, 900, coarse_step_s=COARSE_STEP_S)
    )

    assert len(found) == 1
    assert seconds_at(found[0].aos) == pytest.approx(0.0, abs=TIME_TOLERANCE_S)
    assert seconds_at(found[0].los) == pytest.approx(600.0, abs=TIME_TOLERANCE_S)
    assert found[0].is_truncated is False


def test_a_raised_floor_shortens_the_pass_by_the_amount_arcsin_says() -> None:
    """A 30 degree floor on a 60 degree pass.

    ``60 sin(2 pi t / 1200) = 30`` gives ``t = (1200 / 2 pi) arcsin(0.5)``, and
    ``arcsin(0.5)`` is ``pi / 6``, so ``t = 1200 / 12 = 100`` seconds. The pass
    is symmetric about its culmination at 300 s, so it ends at 500 s.
    """
    found = find_elevation_passes(
        sine_elevation_deg,
        scan_of(-300, 900, min_elevation_deg=30.0, coarse_step_s=COARSE_STEP_S),
    )

    assert len(found) == 1
    assert seconds_at(found[0].aos) == pytest.approx(100.0, abs=TIME_TOLERANCE_S)
    assert seconds_at(found[0].los) == pytest.approx(500.0, abs=TIME_TOLERANCE_S)


def test_the_floor_searched_against_is_carried_on_the_result() -> None:
    """Without it a stored pass is uninterpretable — see ``PassWindow``."""
    found = find_elevation_passes(
        sine_elevation_deg,
        scan_of(-300, 900, min_elevation_deg=30.0, coarse_step_s=COARSE_STEP_S),
    )
    assert found[0].min_elevation_deg == 30.0


def test_the_culmination_is_refined_rather_than_the_best_coarse_sample() -> None:
    """The sine peaks at 300 s at 60 degrees.

    With a 35 s step from −300 s the nearest samples are 295 s and 330 s, so an
    implementation returning its best sample would answer one of those. Both are
    outside the assertion below.
    """
    found = find_elevation_passes(
        sine_elevation_deg, scan_of(-300, 900, coarse_step_s=COARSE_STEP_S)
    )

    assert seconds_at(found[0].max_elevation_at) == pytest.approx(
        300.0, abs=CULMINATION_TOLERANCE_S
    )
    assert found[0].max_elevation_deg == pytest.approx(AMPLITUDE_DEG, abs=0.01)


def test_two_periods_give_two_passes_in_ascending_order() -> None:
    """Passes at 0–600 s and 1200–1800 s, returned earliest first."""
    found = find_elevation_passes(
        sine_elevation_deg, scan_of(-300, 2100, coarse_step_s=COARSE_STEP_S)
    )

    assert len(found) == 2
    assert seconds_at(found[0].aos) == pytest.approx(0.0, abs=TIME_TOLERANCE_S)
    assert seconds_at(found[1].aos) == pytest.approx(1200.0, abs=TIME_TOLERANCE_S)
    assert found[0].los < found[1].aos


def test_a_floor_above_the_highest_elevation_finds_nothing() -> None:
    """Empty list, not a zero-length window."""
    found = find_elevation_passes(
        sine_elevation_deg,
        scan_of(-300, 900, min_elevation_deg=70.0, coarse_step_s=COARSE_STEP_S),
    )
    assert found == []


def test_a_pass_already_under_way_at_the_scan_start_is_marked_truncated() -> None:
    """Its AOS is the edge of the search, not a horizon crossing.

    The scan opens at 300 s, when the satellite is at its culmination. Reporting
    that as an acquisition without saying so would let a completeness
    denominator count the same pass twice — once here, once in the scan that
    covers the real rise.
    """
    found = find_elevation_passes(
        sine_elevation_deg, scan_of(300, 900, coarse_step_s=COARSE_STEP_S)
    )

    assert len(found) == 1
    assert found[0].is_truncated is True
    assert seconds_at(found[0].aos) == pytest.approx(300.0, abs=TIME_TOLERANCE_S)
    assert seconds_at(found[0].los) == pytest.approx(600.0, abs=TIME_TOLERANCE_S)


def test_a_coarse_step_longer_than_the_pass_misses_it_entirely() -> None:
    """``coarse_step_s`` is a correctness parameter, and this is the proof.

    ``PassSearch.coarse_step_s`` claims a step longer than the shortest pass
    does not find it late — it does not find it at all. Both directions are
    asserted here, because a claim that strong should fail loudly if it stops
    being true: the same 120-second pass is found at the 30 s default and is
    absent at 600 s, where the samples at 0, 600 and 1200 seconds all sit below
    the horizon while the pass runs between two of them.
    """
    at_default = find_elevation_passes(
        one_short_pass_deg, scan_of(0, 1200, coarse_step_s=30.0)
    )
    at_oversized = find_elevation_passes(
        one_short_pass_deg, scan_of(0, 1200, coarse_step_s=600.0)
    )

    assert len(at_default) == 1
    assert seconds_at(at_default[0].aos) == pytest.approx(200.0, abs=TIME_TOLERANCE_S)
    assert seconds_at(at_default[0].los) == pytest.approx(320.0, abs=TIME_TOLERANCE_S)
    assert at_oversized == []


def test_an_empty_or_reversed_interval_finds_nothing() -> None:
    assert find_elevation_passes(sine_elevation_deg, scan_of(300, 300)) == []
    assert find_elevation_passes(sine_elevation_deg, scan_of(900, 300)) == []


def test_a_non_positive_coarse_step_is_refused() -> None:
    """Zero would ask for an unbounded number of samples."""
    with pytest.raises(ValueError, match="coarse_step_s"):
        find_elevation_passes(sine_elevation_deg, scan_of(-300, 900, coarse_step_s=0.0))


def test_reaching_the_floor_exactly_counts_as_being_above_it() -> None:
    """``ElevationScan.min_elevation_deg`` says "at or above", and this pins it.

    The curve touches the floor at 260 s and is below it at every other instant,
    and the 130 s step puts a sample exactly there. The window is zero length,
    which is the honest answer for a satellite that grazed the floor: the
    geometry says a pass existed, and whether one that brief is worth a
    station's time is the scheduler's judgement, not this module's. Inventing a
    minimum duration here would put a threshold nobody has evidence for inside
    the completeness denominator.
    """
    found = find_elevation_passes(
        grazing_pass_deg, scan_of(0, 600, coarse_step_s=130.0)
    )

    assert len(found) == 1
    assert seconds_at(found[0].aos) == pytest.approx(260.0, abs=TIME_TOLERANCE_S)
    assert found[0].los == found[0].aos
    assert found[0].max_elevation_deg == pytest.approx(0.0, abs=0.01)


def test_the_scan_does_not_look_past_its_own_end() -> None:
    """A pass beginning after ``end`` belongs to the next scan, not this one.

    The 120-second pass runs from 200 s; the scan closes at 150 s with a 100 s
    step, which divides unevenly. Rounding the sample count up instead of down
    would place a sample at 200 s — outside the interval asked for — and report
    a pass the caller did not ask about. Under the boundary rule that keys
    passes on acquisition, that pass would then be returned by two adjacent
    scans and counted twice.
    """
    found = find_elevation_passes(
        one_short_pass_deg, scan_of(0, 150, coarse_step_s=100.0)
    )
    assert found == []


def test_a_naive_datetime_is_refused_at_this_boundary() -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    naive = ElevationScan(
        at_seconds(-300).replace(tzinfo=None), at_seconds(900), coarse_step_s=30.0
    )
    with pytest.raises(ValueError, match="naive"):
        find_elevation_passes(sine_elevation_deg, naive)

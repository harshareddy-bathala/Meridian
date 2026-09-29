"""The declared horizon, read as a step function and applied to a track.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-031, D-175.
"""

from __future__ import annotations

import pytest

from meridian.scheduler.declared_horizon import DeclaredMask, clears_any

NORTH_WALL = DeclaredMask.from_stored(
    [{"az_deg": 90.0, "min_el_deg": 8.0}, {"az_deg": 315.0, "min_el_deg": 30.0}]
)
"""A building from 315° round through north to 90°, open sky elsewhere."""


@pytest.mark.parametrize(
    ("azimuth", "floor"),
    [
        (315.0, 30.0),
        (359.9, 30.0),
        (0.0, 30.0),
        (89.9, 30.0),
        (90.0, 8.0),
        (200.0, 8.0),
        (314.9, 8.0),
        (720.0, 30.0),
    ],
)
def test_each_point_holds_clockwise_to_the_next_wrapping_at_north(
    azimuth: float, floor: float
) -> None:
    assert NORTH_WALL.floor_at(azimuth) == floor


def test_a_lone_point_holds_all_the_way_round() -> None:
    mask = DeclaredMask.from_stored([{"az_deg": 180.0, "min_el_deg": 12.0}])

    assert {mask.floor_at(azimuth) for azimuth in (0.0, 90.0, 180.0, 270.0)} == {12.0}


def test_two_points_at_one_azimuth_keep_the_higher_floor() -> None:
    """The reading that promises less (D-175)."""
    mask = DeclaredMask.from_stored(
        [{"az_deg": 0.0, "min_el_deg": 5.0}, {"az_deg": 0.0, "min_el_deg": 20.0}]
    )

    assert mask.floor_at(0.0) == 20.0


def test_the_order_points_are_declared_in_does_not_matter() -> None:
    shuffled = DeclaredMask.from_stored(
        [{"az_deg": 315.0, "min_el_deg": 30.0}, {"az_deg": 90.0, "min_el_deg": 8.0}]
    )

    assert shuffled == NORTH_WALL


def test_a_pass_is_blocked_only_if_no_sample_clears() -> None:
    behind_the_wall = [(350.0, 12.0), (10.0, 25.0), (60.0, 20.0)]
    over_the_wall = [(350.0, 12.0), (10.0, 31.0)]
    into_open_sky = [(80.0, 20.0), (100.0, 9.0)]

    assert not NORTH_WALL.clears(behind_the_wall)
    assert NORTH_WALL.clears(over_the_wall)
    assert NORTH_WALL.clears(into_open_sky)


def test_touching_the_floor_is_not_clearing_it() -> None:
    assert not NORTH_WALL.clears([(200.0, 8.0)])


def test_an_empty_mask_constrains_nothing() -> None:
    empty = DeclaredMask.from_stored([])

    assert empty.empty
    assert empty.clears([(0.0, -5.0)])
    with pytest.raises(ValueError, match="no floor"):
        empty.floor_at(0.0)


def test_a_pass_is_kept_if_any_chain_that_could_receive_it_sees_it() -> None:
    """Two antennas are a constraint only where both are blocked."""
    south_wall = DeclaredMask.from_stored([{"az_deg": 0.0, "min_el_deg": 40.0}])
    track = [(10.0, 25.0)]

    assert not clears_any([NORTH_WALL, south_wall], track)
    assert clears_any([NORTH_WALL, DeclaredMask.from_stored([])], track)
    assert clears_any([], track)
    assert clears_any(
        [NORTH_WALL, DeclaredMask.from_stored([{"az_deg": 0.0, "min_el_deg": 5.0}])],
        track,
    )

"""The publication rounding, as a table of numbers.

``coordinate_privacy`` is pure, so every case here is an input and an expected
output with no fixture between them. That is the point of the module being pure:
the rule a station is trusting with its address can be read off a table rather
than inferred from a response body.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-082; docs/MSP-SPEC.md §4.1.
"""

from __future__ import annotations

import pytest

from meridian.api.public.coordinate_privacy import (
    COARSEST_DECIMALS,
    FINEST_DECIMALS,
    publish_location,
)

BENGALURU_LAT_DEG = 12.971598
BENGALURU_LON_DEG = 77.594562
"""Station 001's site, at the six decimal places MSP §4.1 permits at most.

A real coordinate rather than a round number, because a fixture like ``10.0``
rounds to itself at every setting and would pass whatever the function did.
"""


@pytest.mark.parametrize(
    ("decimals", "expected_lat_deg", "expected_lon_deg"),
    [
        (1, 13.0, 77.6),
        (2, 12.97, 77.59),
        (3, 12.972, 77.595),
        (4, 12.9716, 77.5946),
        (5, 12.9716, 77.59456),
        (6, 12.971598, 77.594562),
    ],
)
def test_each_declared_precision_publishes_that_many_decimals(
    decimals: int, expected_lat_deg: float, expected_lon_deg: float
) -> None:
    """The whole rule, one row per value the protocol permits.

    The 5-decimal latitude is ``12.9716`` rather than ``12.97160`` because a
    trailing zero is not a float, which is worth seeing in the table rather than
    discovering in a failing assertion.
    """
    published = publish_location(BENGALURU_LAT_DEG, BENGALURU_LON_DEG, 920.4, decimals)

    assert published.lat_deg == expected_lat_deg
    assert published.lon_deg == expected_lon_deg


def test_the_coarsest_setting_moves_the_site_off_its_own_street() -> None:
    """One decimal is roughly 11 km, which is the point of offering it.

    Asserted as a distance rather than a value so the test says what the setting
    buys an operator: a pin somewhere in the city, not on their roof.
    """
    published = publish_location(
        BENGALURU_LAT_DEG, BENGALURU_LON_DEG, 920.0, COARSEST_DECIMALS
    )

    assert abs(published.lat_deg - BENGALURU_LAT_DEG) > 0.02


def test_altitude_is_whole_metres_whatever_precision_was_asked_for() -> None:
    """Altitude is decoupled from the field on purpose (D-082).

    Sub-metre altitude is instrument noise, not an address, so the finest
    coordinate setting still publishes a whole number of metres.
    """
    published = publish_location(
        BENGALURU_LAT_DEG, BENGALURU_LON_DEG, 920.4, FINEST_DECIMALS
    )

    assert published.alt_m == 920
    assert isinstance(published.alt_m, int)


@pytest.mark.parametrize(
    ("lat_deg", "lon_deg"), [(-12.971598, -77.594562), (-0.04, -0.04)]
)
def test_a_southern_or_western_site_rounds_towards_the_same_grid(
    lat_deg: float, lon_deg: float
) -> None:
    """Negative coordinates are not a special case, and this proves it.

    Worth an explicit test because a sign error here would place a station in the
    wrong hemisphere while still looking like a plausible coordinate — the class
    of bug that survives a glance at a map.
    """
    published = publish_location(lat_deg, lon_deg, 0.0, 2)

    assert published.lat_deg == round(lat_deg, 2)
    assert published.lon_deg == round(lon_deg, 2)
    assert published.lat_deg <= 0
    assert published.lon_deg <= 0


@pytest.mark.parametrize(
    ("lat_deg", "lon_deg", "decimals"), [(89.99, 179.99, 1), (-89.99, -179.99, 1)]
)
def test_rounding_never_pushes_a_coordinate_out_of_range(
    lat_deg: float, lon_deg: float, decimals: int
) -> None:
    """The poles and the antimeridian round *onto* their limits, not past them.

    89.99 becomes 90.0 and 179.99 becomes 180.0, both of which ISO 6709 and the
    `stations` CHECK constraints still accept. No wrapping is needed, which is
    why the module does none — this test is what says that is a property rather
    than an omission.
    """
    published = publish_location(lat_deg, lon_deg, 0.0, decimals)

    assert -90.0 <= published.lat_deg <= 90.0
    assert -180.0 <= published.lon_deg <= 180.0


@pytest.mark.parametrize("decimals", [0, -1, 7])
def test_a_precision_the_column_cannot_hold_is_refused(decimals: int) -> None:
    """Failing beats publishing at a precision nobody agreed to.

    A caller passing 0 or 7 has read the figure from somewhere other than the
    station's row, because the CHECK constraint permits neither. Rounding to it
    anyway would disclose a position under a setting the operator never chose.
    """
    with pytest.raises(ValueError, match="location_precision_decimals"):
        publish_location(BENGALURU_LAT_DEG, BENGALURU_LON_DEG, 920.0, decimals)

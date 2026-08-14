"""Coarsening a station's coordinates for publication.

A station declares `location_precision_decimals` at registration (MSP §4.1), and
this is the one place that figure is applied. Every public response that carries a
station's position builds it here, so the platform has a single answer to "how
precisely is this site disclosed" rather than one answer per endpoint.

The stored coordinates are never touched. `store.receiving_stations`'s read
serves them at full precision to pass generation and the scheduler, which need
them: a latitude coarsened to one decimal moves the site by up to 11 km and shifts
predicted acquisition by seconds. Rounding happens on the way out and nowhere
earlier — this module is the "on the way out".

It is pure computation. No I/O, no database, no framework, so the whole
publication rule is testable as a table of numbers.

Reference: docs/DECISIONS.md D-082; docs/MSP-SPEC.md §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "COARSEST_DECIMALS",
    "FINEST_DECIMALS",
    "PublishedLocation",
    "publish_location",
]

COARSEST_DECIMALS = 1
FINEST_DECIMALS = 6
"""The range MSP §4.1 permits, matched by the ``CHECK`` on the column.

One decimal is roughly 11 km of latitude and six is roughly 11 cm, both at the
equator. Longitude's true ground distance shrinks with ``cos(latitude)``, so these
figures are an upper bound on what a station discloses and never a lower one —
the safe direction for a privacy control, and the reason the field counts decimals
rather than metres.
"""


@dataclass(frozen=True, slots=True)
class PublishedLocation:
    """A station's position as the public API is allowed to state it.

    A distinct type from the stored coordinates on purpose. The two are both
    triples of numbers and are trivial to confuse at a call site, and confusing
    them in the wrong direction publishes a rooftop.
    """

    lat_deg: float
    lon_deg: float

    alt_m: int
    """Always whole metres, whatever precision the station asked for.

    Sub-metre altitude is instrument noise rather than an address, so coupling it
    to the declared figure would be one more thing to reason about for no privacy
    gained. An integer rather than a rounded float so the response cannot carry
    ``920.0`` and invite a reader to wonder what the ``.0`` is standing in for.
    """


def publish_location(
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    decimals: int,
) -> PublishedLocation:
    """Coarsen one station's position to the precision its operator declared.

    Args:
        lat_deg: Stored latitude, full precision, -90 to 90.
        lon_deg: Stored longitude, full precision, -180 to 180.
        alt_m: Stored altitude above the ellipsoid, in metres.
        decimals: The station's ``location_precision_decimals``, 1 to 6.

    Returns:
        The position as it may be published. Never finer than ``decimals``.

    Raises:
        ValueError: ``decimals`` is outside 1 to 6. A caller that passed a value
            the column cannot hold has read the figure from somewhere other than
            the station's row, and failing here is better than publishing a
            coordinate at a precision nobody agreed to.

    Note:
        Rounding half-to-even, which is Python's ``round``. Which way a tie goes
        is not a privacy question — both neighbours are equally far from the true
        position, and at the coarsest setting the tie is 5.5 km either way — but
        it is stated because the function is otherwise deterministic and a
        reader comparing two values a whole-metre apart deserves to know why.

        Rounding cannot push a value out of range: 89.99 rounds to 90.0 and
        179.99 to 180.0, both of which are still valid. No wrapping is needed and
        none is done.
    """
    if not COARSEST_DECIMALS <= decimals <= FINEST_DECIMALS:
        raise ValueError(
            f"location_precision_decimals must be {COARSEST_DECIMALS} to "
            f"{FINEST_DECIMALS}, got {decimals}"
        )

    return PublishedLocation(
        lat_deg=round(lat_deg, decimals),
        lon_deg=round(lon_deg, decimals),
        alt_m=round(alt_m),
    )

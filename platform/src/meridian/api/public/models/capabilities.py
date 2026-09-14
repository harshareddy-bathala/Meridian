"""What a station declared it can receive with, as the public API states it.

Turns a ``store.station_capabilities.StoredCapability`` into a response body. The
only work on the way is renaming the horizon mask's keys: the column stores
``az_deg`` and ``min_el_deg``, which D-031 fixed and which do not change, and the
published names spell themselves out and carry their units. Translating between
the two is this layer's job in both directions — ``meridian.api.models
.registration`` performs the same swap on the way in.

Nothing here is coarsened or withheld. A capability is a claim an operator
published about their own hardware, not a fact about where they live, so unlike a
station's coordinates it is served exactly as declared.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel

from meridian.store.station_capabilities import StoredCapability

__all__ = ["HorizonMaskPoint", "PublicCapability"]


class HorizonMaskPoint(BaseModel):
    """One bearing of a declared obstruction, and the elevation it reaches."""

    azimuth_deg: float
    min_elevation_deg: float


class PublicCapability(BaseModel):
    """One antenna and receiver chain a station declared at registration."""

    band: str
    freq_min_hz: int
    freq_max_hz: int
    modes: list[str]
    polarisation: str

    tracking: bool
    """Whether the antenna follows the satellite. A fixed antenna is not a lesser
    station — station 001's own quadrifilar helix does not move — but it changes
    which passes are worth attempting, so a reader comparing two stations needs
    it."""

    min_elevation_deg: float
    """What the operator **declared**, not what the platform later measured.

    The two are kept apart deliberately (D-031): a declaration is a claim and a
    learned horizon is evidence, and publishing them as one number would let a
    claim be read as a measurement.
    """

    horizon_mask: list[HorizonMaskPoint]
    """The declared obstruction, empty when the operator described none.

    Empty means "nothing was declared", which is not the same as "the horizon is
    clear" — most operators simply do not survey their site.
    """

    @classmethod
    def from_row(cls, row: StoredCapability) -> Self:
        """Build the response body for one stored capability.

        Args:
            row: The capability as the store returned it. Its ``horizon_mask`` is
                already a decoded list — the column is ``jsonb`` and psycopg
                returns Python objects, so there is no text to parse.

        Returns:
            The capability, with the mask's keys renamed to the published ones.
        """
        return cls(
            band=row.band,
            freq_min_hz=row.freq_min_hz,
            freq_max_hz=row.freq_max_hz,
            modes=list(row.modes),
            polarisation=row.polarisation,
            tracking=row.tracking,
            min_elevation_deg=row.min_elevation_deg,
            horizon_mask=[
                HorizonMaskPoint(
                    azimuth_deg=point["az_deg"],
                    min_elevation_deg=point["min_el_deg"],
                )
                for point in row.horizon_mask
            ],
        )

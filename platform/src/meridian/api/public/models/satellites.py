"""The satellite catalogue, as the public API states it.

Turns ``store.satellite_catalogue`` rows into response bodies. One thing is
computed on the way: an element set's **age**, from the epoch the store returned
and a clock reading the route takes once per request. That mirrors how liveness
is handled (D-054) and for the same reason — a list aged row by row would measure
each satellite against a slightly different "now", and the differences would be
small enough to look like data.

Nothing here is coarsened or withheld. A satellite is a public object in a public
catalogue; the privacy rule that shapes the station models has no analogue here.

Neither model carries ``simulated``. The catalogue holds real spacecraft, and
neither table has the column — a simulator run invents stations and observations,
not satellites. Provenance for a transmitter travels in ``source`` instead.

Reference: docs/DECISIONS.md D-021, D-066; CLAUDE.md (element-set age is a
first-class feature everywhere).
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.store.satellite_catalogue import (
    CataloguedSatellite,
    CataloguedTransmitter,
)

__all__ = ["PublicSatellite", "PublicTransmitter"]


def _element_set_age_s(epoch: datetime | None, now: datetime) -> float | None:
    """Seconds from an element set's epoch to ``now``, or ``None`` if we hold none.

    Negative values are returned unchanged and are not an error: element sets are
    routinely published with an epoch a little ahead of their release, so a fresh
    one can legitimately describe a moment that has not happened yet. Clamping to
    zero would hide the freshest data the archive holds behind the same number as
    a set that arrived this second.
    """
    if epoch is None:
        return None
    return (now - epoch).total_seconds()


class PublicSatellite(BaseModel):
    """One tracked object, as a reader receives it."""

    satellite_id: str
    name: str
    orbital_regime: str

    priority: float
    """The operator weighting the scheduler multiplies elevation by (D-066).

    Published because it is half the answer to "why was that pass chosen over
    this one". 1.0 is the default, and the value at which configuration B
    reduces to configuration A exactly.
    """

    is_active: bool
    """Whether we still expect to hear this object transmitting.

    False says the spacecraft is believed silent, not that our records are
    incomplete — which is the distinction that stops a decommissioned payload's
    empty week reading as a prediction failure.
    """

    latest_element_set_epoch: datetime | None
    """The newest epoch held for this satellite, or ``null`` if we hold none."""

    element_set_age_s: float | None
    """How stale that element set is, in seconds, or ``null`` alongside a ``null``
    epoch.

    Published beside the epoch rather than instead of it: the age is what a
    reader wants and the epoch is what makes the age checkable against the
    response's own timestamp. ``null`` means a tracked object we cannot currently
    propagate — a gap, not an age of zero.
    """

    @classmethod
    def from_row(cls, row: CataloguedSatellite, *, now: datetime) -> Self:
        """Build the response body for one catalogued satellite.

        Args:
            row: The satellite as the store returned it.
            now: The clock reading every satellite in this response is aged
                against. Passed in rather than read here so one page shares one
                instant.

        Returns:
            The satellite, with its element-set age derived.
        """
        return cls(
            satellite_id=row.satellite_id,
            name=row.name,
            orbital_regime=row.orbital_regime,
            priority=row.priority,
            is_active=row.is_active,
            latest_element_set_epoch=row.latest_element_set_epoch,
            element_set_age_s=_element_set_age_s(row.latest_element_set_epoch, now),
        )


class PublicTransmitter(BaseModel):
    """One downlink of one satellite, as a reader receives it."""

    satellite_id: str
    centre_freq_hz: int
    """Nominal, unshifted, in Hz as an integer — never MHz and never a float.

    What a station would tune to at the start of a pass, before Doppler. At
    137 MHz in low Earth orbit the observed frequency swings roughly ±3 kHz
    either side of this during a pass.
    """

    mode: str
    polarisation: str | None
    """``null`` where the polarisation is simply not recorded, which is a real
    entry rather than a missing one — most catalogue sources do not state it."""

    bandwidth_hz: int | None

    is_active: bool
    """Whether this downlink is expected to be heard, independent of whether its
    satellite is. A live spacecraft can have one transmitter switched off."""

    source: str
    """``manual`` or ``simulator`` — where the entry came from. Published for the
    reason ``simulated`` is published everywhere else: an entry invented for a
    simulator run must not be readable as a fact about a real spacecraft."""

    @classmethod
    def from_row(cls, row: CataloguedTransmitter) -> Self:
        """Build the response body for one catalogued transmitter.

        Args:
            row: The transmitter as the store returned it.

        Returns:
            The transmitter, unchanged — nothing about a downlink is derived.
        """
        return cls(
            satellite_id=row.satellite_id,
            centre_freq_hz=row.centre_freq_hz,
            mode=row.mode,
            polarisation=row.polarisation,
            bandwidth_hz=row.bandwidth_hz,
            is_active=row.is_active,
            source=row.source,
        )

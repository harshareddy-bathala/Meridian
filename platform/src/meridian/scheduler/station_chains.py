"""A station's receiving chains, and whether one of them sees a pass (D-175).

Each chain is a declared capability with its declared horizon mask. A pass is a
candidate only if some chain that can receive its downlink sees its track over
that chain's mask. The rule itself is :mod:`meridian.scheduler.declared_horizon`,
a leaf; this module reads the chains and computes the track it is applied to.

Split out of ``candidates`` so that module holds the candidate set and this one
the mask it is filtered by.

Reference: docs/DECISIONS.md D-031, D-175.
"""

from __future__ import annotations

from collections.abc import Sequence

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.registry.capability_match import (
    ReceiveCapability,
    covers_transmission,
)
from meridian.scheduler.declared_horizon import DeclaredMask, clears_any
from meridian.scheduler.live_inputs import TRACK_STEP_S
from meridian.store.passes import StoredPass
from meridian.store.satellites import StoredTransmitter
from meridian.store.station_capabilities import find_capabilities_for_station
from meridian.store.stations import Connection

__all__ = ["clears_declared_horizon", "load_chains"]


def load_chains(
    conn: Connection, station_id: str
) -> list[tuple[ReceiveCapability, DeclaredMask]]:
    """One station's declared receiving chains, each with its declared mask."""
    return [
        (
            ReceiveCapability(
                freq_min_hz=stored.freq_min_hz,
                freq_max_hz=stored.freq_max_hz,
                modes=tuple(stored.modes),
                min_elevation_deg=stored.min_elevation_deg,
            ),
            DeclaredMask.from_stored(stored.horizon_mask),
        )
        for stored in find_capabilities_for_station(conn, station_id)
    ]


def clears_declared_horizon(  # noqa: PLR0913 — each is one fact the test needs
    orbit: OrbitService,
    *,
    element_set: ElementSet,
    site: GroundSite,
    stored: StoredPass,
    transmitter: StoredTransmitter,
    chains: Sequence[tuple[ReceiveCapability, DeclaredMask]],
) -> bool:
    """Whether a chain that can receive this downlink sees the pass over its mask.

    The track is sampled as live scoring samples it (D-169). It is computed only
    when every covering chain declares a mask, so a station that declares none
    propagates nothing more than it did before (D-175).
    """
    masks = [
        mask
        for capability, mask in chains
        if covers_transmission(capability, transmitter.centre_freq_hz, transmitter.mode)
    ]
    if not masks or any(mask.empty for mask in masks):
        return True
    angles = orbit.look_angles(
        element_set, site, stored.aos, stored.los, step_s=TRACK_STEP_S
    )
    track = [(one.azimuth_deg % 360.0, one.elevation_deg) for one in angles]
    return clears_any(masks, track)

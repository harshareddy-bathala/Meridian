"""Whether a station's declared hardware can receive a given transmission.

A station registers one capability per antenna and receiver chain (MSP §4.1): a
frequency range, the demodulators it can run, and the elevation floor below
which its own site is obstructed. A satellite carries transmitters, each with a
nominal centre frequency and a mode. This module answers, for one pair, whether
the geometry is worth computing at all — a station that cannot tune to 137 MHz
has no passes of Meteor-M, however clear its sky.

A leaf, like ``registry.liveness`` and ``registry.doppler_tolerance``: it imports
nothing from ``meridian``, performs no I/O and reads no clock, so it can be
exercised on plain numbers and strings. The pass-generation job calls it to
narrow the station-by-satellite grid before propagating anything, and the
scheduler calls it for the same question about a candidate pass.

Frequencies are integers in Hz throughout. Elevations are degrees above the
local horizon.

Reference: docs/MSP-SPEC.md §4.1 (the capability object); docs/DECISIONS.md
D-064 (what counts as a match, and what a station declaring no mode gets).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "ReceiveCapability",
    "covering_capabilities",
    "covers_transmission",
    "lowest_elevation_floor_deg",
]


@dataclass(frozen=True, slots=True)
class ReceiveCapability:
    """One antenna and receiver chain, as the station declared it.

    The four fields of ``station_capabilities`` that bear on whether a
    transmission is receivable. Band, polarisation, tracking and the horizon
    mask are deliberately absent: band is a label over the frequency range
    rather than an independent constraint, and the other three change how well a
    pass is received rather than whether it can be attempted at all.
    """

    freq_min_hz: int
    freq_max_hz: int

    modes: tuple[str, ...]
    """The demodulators this chain can run, lowercase free text in Phase 1.

    Free text because decoder naming varies too much across projects to freeze
    an enum this early — ``station_capabilities.modes`` says the same.
    """

    min_elevation_deg: float
    """The station's declared floor for this chain, not its measured horizon."""


def covers_transmission(
    capability: ReceiveCapability, centre_freq_hz: int, mode: str
) -> bool:
    """Whether this chain can tune to a transmission and demodulate it.

    Both conditions are required. A station tuned to the right frequency running
    the wrong demodulator receives nothing, which is the same reason
    ``heartbeats.listening_mode`` exists (D-028).

    Args:
        capability: One declared antenna and receiver chain.
        centre_freq_hz: The transmitter's nominal frequency in Hz — the
            unshifted one from the catalogue, not a Doppler-shifted observation.
        mode: The transmitter's modulation, matched case-insensitively against
            the chain's declared modes.

    Returns:
        True when the nominal frequency falls inside the declared range,
        endpoints included, and the mode is one the chain declared.

    Note:
        **The nominal frequency is what is tested, not the range the signal will
        actually sweep.** A pass Doppler-shifts by roughly ±3 kHz at 137 MHz, so
        a chain whose declared range ends within a few kilohertz of the nominal
        loses part of the excursion. Phase 1 does not model that: the declared
        range is hardware the operator described, and second-guessing it would
        exclude working stations on a margin nobody has measured. The failure it
        leaves is visible in the observation record — reception degrading at one
        end of a pass — rather than silent.
    """
    within_range = capability.freq_min_hz <= centre_freq_hz <= capability.freq_max_hz
    return within_range and mode.strip().lower() in _normalised_modes(capability)


def _normalised_modes(capability: ReceiveCapability) -> frozenset[str]:
    """The chain's declared modes, lowercased and stripped for comparison."""
    return frozenset(declared.strip().lower() for declared in capability.modes)


def covering_capabilities(
    capabilities: Sequence[ReceiveCapability], centre_freq_hz: int, mode: str
) -> list[ReceiveCapability]:
    """Every one of a station's chains that can receive this transmission.

    Args:
        capabilities: All chains one station declared. May be empty.
        centre_freq_hz: The transmitter's nominal frequency in Hz.
        mode: The transmitter's modulation.

    Returns:
        The matching chains in the order given, empty when none matches. An
        empty result is the answer "this station has no business being scheduled
        for this transmitter", and the caller is expected to skip the pair
        rather than fall back to a default.

    Note:
        A station may declare several chains — a VHF fixed antenna and a UHF
        tracking one are different hardware with different floors — and more
        than one can cover the same transmission where their ranges overlap.
        Which of them the operator actually uses is theirs to decide; the
        platform only needs to know a pass is receivable, and by
        :func:`lowest_elevation_floor_deg` at what floor.

        **A chain declaring no modes matches nothing.** Declaring an empty list
        is not declaring universal capability: it says nothing about what the
        station can demodulate, and treating silence as "anything" would
        schedule a station for passes it cannot decode. Those become confirmed
        misses — the station heartbeats, listens, and returns no frames — which
        is the one outcome the reliability layer must not be fed by us on
        purpose. A station that gets no passes notices; a reliability figure
        quietly poisoned by passes nobody could have decoded does not.
    """
    return [
        capability
        for capability in capabilities
        if covers_transmission(capability, centre_freq_hz, mode)
    ]


def lowest_elevation_floor_deg(capabilities: Sequence[ReceiveCapability]) -> float:
    """The most permissive declared floor among these chains.

    Args:
        capabilities: One or more chains, normally the result of
            :func:`covering_capabilities`.

    Returns:
        The smallest ``min_elevation_deg`` of the group, in degrees.

    Raises:
        ValueError: ``capabilities`` is empty. There is no floor to report and
            no defensible default: guessing zero would search the geometric
            horizon for a station that declared it cannot see there, and
            guessing anything else would invent a threshold.

    Note:
        The lowest rather than an average or the primary chain's, because the
        station can receive the pass if *any* of its hardware can. Searching
        against a higher floor would drop passes one of its antennas could have
        taken, and a pass excluded here is not merely unscheduled — it never
        enters the completeness denominator, and the ratio silently improves.
    """
    if not capabilities:
        raise ValueError("capabilities is empty; there is no floor to report")

    return min(capability.min_elevation_deg for capability in capabilities)

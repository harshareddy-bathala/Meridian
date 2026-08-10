"""``capability_match`` against declared hardware written out by hand.

Every capability below is a plain set of numbers and strings, so the expected
answer is arithmetic a reader can check in their head rather than something the
function under test computed. Real values are used where they exist: the
Meteor-M LRPT downlink at 137.1 MHz is this project's primary reception target,
and NOAA at 137.9125 MHz is the nearest neighbour in the band.

Marked as a unit test by living in ``tests/unit``: no database, no network and
no element set.
"""

from __future__ import annotations

import pytest

from meridian.registry.capability_match import (
    ReceiveCapability,
    covering_capabilities,
    covers_transmission,
    lowest_elevation_floor_deg,
)

METEOR_LRPT_HZ = 137_100_000
"""Meteor-M LRPT, the primary reception target (CLAUDE.md, domain notes)."""

NOAA_APT_HZ = 137_912_500
"""NOAA at 137.9125 MHz — in the same band, and the closest neighbour to it."""

UHF_BEACON_HZ = 437_500_000
"""A 70 cm cubesat beacon: outside the VHF chain entirely."""


def vhf_chain(
    *,
    modes: tuple[str, ...] = ("lrpt",),
    min_elevation_deg: float = 10.0,
) -> ReceiveCapability:
    """A 137 MHz chain covering the whole VHF satellite band."""
    return ReceiveCapability(
        freq_min_hz=136_000_000,
        freq_max_hz=138_000_000,
        modes=modes,
        min_elevation_deg=min_elevation_deg,
    )


def uhf_chain(min_elevation_deg: float = 5.0) -> ReceiveCapability:
    """A 70 cm chain, a second antenna on the same station."""
    return ReceiveCapability(
        freq_min_hz=430_000_000,
        freq_max_hz=440_000_000,
        modes=("fsk", "bpsk"),
        min_elevation_deg=min_elevation_deg,
    )


# --- one chain against one transmission ---------------------------------------


def test_a_chain_covering_the_frequency_and_the_mode_matches() -> None:
    assert covers_transmission(vhf_chain(), METEOR_LRPT_HZ, "lrpt") is True


def test_the_right_frequency_with_the_wrong_demodulator_does_not_match() -> None:
    """A station tuned correctly running the wrong mode receives nothing.

    The same reason ``heartbeats.listening_mode`` exists (D-028): treating this
    as a match would schedule a station for a pass it cannot decode, and the
    failure would arrive later disguised as a confirmed miss.
    """
    assert covers_transmission(vhf_chain(), METEOR_LRPT_HZ, "fsk") is False


def test_the_right_demodulator_out_of_band_does_not_match() -> None:
    """A VHF antenna does not hear 437 MHz however good its decoder is."""
    chain = vhf_chain(modes=("lrpt", "fsk"))
    assert covers_transmission(chain, UHF_BEACON_HZ, "fsk") is False


def test_both_ends_of_the_declared_range_are_included() -> None:
    """``freq_min_hz <= f <= freq_max_hz``, matching the column's own CHECK.

    An operator who declares a range ending exactly at their transmitter's
    frequency has declared they can receive it. Excluding the endpoint would
    drop that station from the denominator over an off-by-one.
    """
    chain = vhf_chain()

    assert covers_transmission(chain, chain.freq_min_hz, "lrpt") is True
    assert covers_transmission(chain, chain.freq_max_hz, "lrpt") is True
    assert covers_transmission(chain, chain.freq_min_hz - 1, "lrpt") is False
    assert covers_transmission(chain, chain.freq_max_hz + 1, "lrpt") is False


def test_mode_comparison_ignores_case_and_surrounding_space() -> None:
    """``modes`` is free text in Phase 1, so it arrives however a station typed it.

    Nothing normalises it on the way into ``station_capabilities``, and rejecting
    ``LRPT`` against a catalogue's ``lrpt`` would be a matching bug dressed up as
    a capability gap.
    """
    chain = vhf_chain(modes=("LRPT", " fsk "))

    assert covers_transmission(chain, METEOR_LRPT_HZ, "lrpt") is True
    assert covers_transmission(chain, METEOR_LRPT_HZ, "  FSK") is True


def test_a_partial_mode_name_is_not_a_match() -> None:
    """Matched whole, not by prefix. ``lrpt`` and ``lrpt_hrpt`` are not the same
    demodulator, and substring matching would silently conflate them."""
    assert covers_transmission(vhf_chain(), METEOR_LRPT_HZ, "lr") is False


# --- a station's whole declaration --------------------------------------------


def test_only_the_chain_that_can_receive_it_is_returned() -> None:
    """A station with two antennas is matched per antenna, not as a whole."""
    chains = [vhf_chain(), uhf_chain()]

    for_meteor = covering_capabilities(chains, METEOR_LRPT_HZ, "lrpt")
    for_beacon = covering_capabilities(chains, UHF_BEACON_HZ, "bpsk")

    assert for_meteor == [chains[0]]
    assert for_beacon == [chains[1]]


def test_a_station_with_no_matching_chain_gets_an_empty_list() -> None:
    """The caller's signal to skip the pair rather than fall back to a default."""
    assert covering_capabilities([vhf_chain()], UHF_BEACON_HZ, "bpsk") == []
    assert covering_capabilities([], METEOR_LRPT_HZ, "lrpt") == []


def test_a_chain_declaring_no_modes_matches_nothing() -> None:
    """Declaring an empty list is silence, not a claim of universal capability.

    ``station_capabilities.modes`` defaults to ``'{}'`` and the API imposes no
    minimum, so this is reachable from a real registration. Reading it as "any
    mode" would schedule the station for every pass in its frequency range and
    feed the reliability layer misses that were guaranteed before the pass
    began.
    """
    silent = vhf_chain(modes=())

    assert covers_transmission(silent, METEOR_LRPT_HZ, "lrpt") is False
    assert covering_capabilities([silent], METEOR_LRPT_HZ, "lrpt") == []


def test_a_neighbouring_satellite_in_the_same_band_still_matches() -> None:
    """Deliberate, and worth stating: the chain is broadband and covers both.

    NOAA at 137.9125 MHz sits 12.5 kHz from Meteor's 137.900 MHz service, and a
    VHF antenna genuinely receives both. This module answers "can the hardware
    receive it", not "is the station listening to it" — that second question is
    ``registry.was_listening``, and it is the one that needs a tolerance narrow
    enough to keep the two apart (D-056).
    """
    chain = vhf_chain(modes=("lrpt", "apt"))

    assert covers_transmission(chain, METEOR_LRPT_HZ, "lrpt") is True
    assert covers_transmission(chain, NOAA_APT_HZ, "apt") is True


# --- the floor to search against ----------------------------------------------


def test_the_lowest_floor_of_the_matching_chains_is_used() -> None:
    """The station can take the pass if any of its hardware can.

    Searching against the higher floor would drop passes the 5-degree antenna
    could have received, and a pass excluded here never enters the completeness
    denominator at all.
    """
    chains = [
        vhf_chain(min_elevation_deg=15.0),
        vhf_chain(modes=("lrpt",), min_elevation_deg=5.0),
    ]

    assert lowest_elevation_floor_deg(chains) == 5.0


def test_one_chain_reports_its_own_floor() -> None:
    assert lowest_elevation_floor_deg([vhf_chain(min_elevation_deg=12.5)]) == 12.5


def test_a_negative_floor_is_carried_rather_than_clamped() -> None:
    """A station on a mountain genuinely sees below the horizontal.

    ``station_capabilities`` allows −90 to +90 and means it; clamping to zero
    here would quietly discard elevation the operator declared they have.
    """
    assert lowest_elevation_floor_deg([vhf_chain(min_elevation_deg=-1.5)]) == -1.5


def test_asking_for_a_floor_with_no_chains_is_refused() -> None:
    """There is no defensible default: zero would search the geometric horizon
    for a station that declared it cannot see there."""
    with pytest.raises(ValueError, match="empty"):
        lowest_elevation_floor_deg([])

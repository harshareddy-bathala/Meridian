"""``meridian_sim.config`` — seeds, and the fleet they produce.

No marker: this is arithmetic and a dataclass. That it needs nothing is the
point — the determinism the whole stage rests on is checkable before any station
speaks MSP, which is why the derivation was kept pure (D-075, D-077).

Reference: docs/DECISIONS.md D-075, D-080.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian_sim.config import (
    MODES,
    VHF_RANGE_HZ,
    RunConfig,
    profile_for_station,
    seed_for_pass,
    seed_for_station,
    state_dir_for_station,
)

MASTER_SEED = 4471
RUN_ID = "run-2026-08-12"

# The two downlinks deploy/catalogue/development.json carries. Written out
# rather than imported: the simulator is a separate distribution and cannot see
# the platform's catalogue, and that separation is exactly the coupling these
# tests exist to catch.
CATALOGUE_TRANSMITTERS = ((137_100_000, "lrpt"), (137_900_000, "lrpt"))


def config(**overrides: object) -> RunConfig:
    """A run configuration with the fields a test varies exposed."""
    values: dict[str, object] = {
        "master_seed": MASTER_SEED,
        "run_id": RUN_ID,
        "station_count": 1,
        "base_url": "http://localhost:8000",
        "state_dir": Path("/var/lib/meridian-sim"),
    }
    values.update(overrides)
    return RunConfig(**values)  # type: ignore[arg-type]


def test_a_seed_is_stable_for_one_master_seed_and_index() -> None:
    """The whole claim, at its smallest: same inputs, same station."""
    assert seed_for_station(MASTER_SEED, 1) == seed_for_station(MASTER_SEED, 1)


def test_each_station_gets_its_own_seed() -> None:
    """A fleet sharing one seed is one station tested many times."""
    seeds = {seed_for_station(MASTER_SEED, index) for index in range(1, 51)}

    assert len(seeds) == 50


def test_a_different_master_seed_moves_every_station() -> None:
    """A new seed is a new network, not the old one with a different label."""
    original = [seed_for_station(MASTER_SEED, index) for index in range(1, 11)]
    other = [seed_for_station(MASTER_SEED + 1, index) for index in range(1, 11)]

    assert not set(original) & set(other)


def test_raising_the_count_cannot_change_station_one() -> None:
    """The roadmap's requirement, true by construction rather than by care.

    Station 1's seed never mentioned the count, or any other station, or
    anything the platform assigned — so there is nothing a larger fleet could
    change about it.
    """
    alone = profile_for_station(1, seed_for_station(MASTER_SEED, 1), RUN_ID)

    for _ in range(50):
        seed_for_station(MASTER_SEED, 50)
    in_a_crowd = profile_for_station(1, seed_for_station(MASTER_SEED, 1), RUN_ID)

    assert alone == in_a_crowd


def test_every_seed_fits_the_column_the_platform_stores_it_in() -> None:
    """``stations.seed`` is a signed ``bigint``, so 63 bits is the whole range.

    Found by a registration failing with `bigint out of range` on station 2. A
    full 64-bit draw overflows for roughly half of all stations, so a fleet of
    one looks fine and a fleet of ten does not — and the error names numeric
    range rather than anything about seeds.
    """
    limit = 2**63 - 1
    station_seeds = [seed_for_station(MASTER_SEED, index) for index in range(1, 501)]

    assert all(0 <= one <= limit for one in station_seeds)
    assert all(
        0 <= seed_for_pass(one, f"as_{index:04d}") <= limit
        for index, one in enumerate(station_seeds)
    )


def test_an_index_below_one_is_refused() -> None:
    """Stations are numbered from one, so a zero is a mistake, not a station."""
    with pytest.raises(ValueError, match="counts from one"):
        seed_for_station(MASTER_SEED, 0)


def test_a_profile_is_stable_for_one_seed() -> None:
    """Two runs at one seed register the same station, down to the altitude."""
    seed = seed_for_station(MASTER_SEED, 7)

    assert profile_for_station(7, seed, RUN_ID) == profile_for_station(7, seed, RUN_ID)


def test_stations_differ_in_where_they_are_and_what_they_will_accept() -> None:
    """A fleet that is one station repeated tests nothing about scheduling."""
    profiles = [
        profile_for_station(index, seed_for_station(MASTER_SEED, index), RUN_ID)
        for index in range(1, 21)
    ]

    assert len({(one.lat_deg, one.lon_deg) for one in profiles}) == 20
    assert len({one.capabilities[0].min_elevation_deg for one in profiles}) > 1


def test_every_station_declares_itself_simulated() -> None:
    """MSP §5, and the flag every derived record inherits from it.

    The platform treats this as authoritative from registration onwards, so a
    station that failed to declare it here would have its observations recorded
    as measured and there would be no later opportunity to correct that.
    """
    profile = profile_for_station(3, seed_for_station(MASTER_SEED, 3), RUN_ID)

    assert profile.simulated is True
    assert profile.simulator_run_id == RUN_ID
    assert profile.seed == seed_for_station(MASTER_SEED, 3)


@pytest.mark.parametrize(("centre_freq_hz", "mode"), CATALOGUE_TRANSMITTERS)
def test_a_station_can_hear_what_the_catalogue_transmits(
    centre_freq_hz: int, mode: str
) -> None:
    """The coupling that decides whether this stage works at all.

    Pass generation asks whether a station's declared chain covers a downlink.
    A fleet whose range excluded the catalogue would register cleanly, heartbeat
    forever and be scheduled for nothing — the hardest failure in this stage to
    trace, because every part of it looks healthy.
    """
    profile = profile_for_station(1, seed_for_station(MASTER_SEED, 1), RUN_ID)
    (chain,) = profile.capabilities

    assert chain.freq_min_hz <= centre_freq_hz <= chain.freq_max_hz
    assert mode in chain.modes


def test_the_declared_band_covers_the_whole_137_megahertz_downlink() -> None:
    """Written as hertz, because a station that declared megahertz would be
    tuned two million times too low and nothing would say so."""
    assert VHF_RANGE_HZ == (136_000_000, 138_000_000)
    assert MODES == ("lrpt",)


def test_an_elevation_floor_is_plausible_for_every_station() -> None:
    """A negative or absurd floor would silently include or exclude every pass."""
    floors = [
        profile_for_station(index, seed_for_station(MASTER_SEED, index), RUN_ID)
        .capabilities[0]
        .min_elevation_deg
        for index in range(1, 51)
    ]

    assert all(5.0 <= one <= 15.0 for one in floors)


def test_a_station_keeps_its_own_directory(tmp_path: Path) -> None:
    """D-080: state is keyed on the index, so a restart finds it again."""
    run = config(state_dir=tmp_path)

    first = state_dir_for_station(run, 1)
    assert first == state_dir_for_station(run, 1)
    assert first != state_dir_for_station(run, 2)


def test_asking_where_a_station_lives_does_not_create_it(tmp_path: Path) -> None:
    """Whether a station has registered before is read from whether this exists."""
    state_dir_for_station(config(state_dir=tmp_path), 1)

    assert list(tmp_path.iterdir()) == []


def test_the_state_path_does_not_carry_the_run_id(tmp_path: Path) -> None:
    """A per-run path could not name state that has to outlive the run (D-080).

    Every station would find an empty directory on the next start, register
    again, and burn an invite doing it.
    """
    one = state_dir_for_station(config(state_dir=tmp_path, run_id="a"), 1)
    other = state_dir_for_station(config(state_dir=tmp_path, run_id="b"), 1)

    assert one == other

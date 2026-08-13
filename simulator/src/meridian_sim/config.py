"""What a simulator run is, and how one station in it differs from the next.

Turns a master seed and a station count into the per-station identities the run
needs: a seed each, and the :class:`~meridian_client.registration.StationProfile`
each will register with. Pure — no clock, no filesystem, no network — so the
whole determinism claim can be checked in a unit test before anything speaks MSP.

Two properties are what this module exists for.

* **A station's identity comes from its index, never from the platform.** The
  seed is derived from the master seed and a one-based index, so a profile can
  be built before registration — which it has to be, because the location and
  capabilities in it are what gets registered (D-075).
* **Raising the station count cannot change station 1.** That follows from the
  first: station 1's seed never mentioned the count, or any other station, or
  anything the platform assigned.

The capabilities generated here have to cover the transmitters in the loaded
catalogue or the two never meet — pass generation asks
``registry.capability_match`` whether a station can hear a downlink, and a
station whose declared range excludes every tracked satellite is scheduled for
nothing and reports nothing, for the rest of the run, silently.

Reference: docs/MSP-SPEC.md §4.1, §5; docs/DECISIONS.md D-075, D-077, D-080.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from meridian_client.registration import ReceiveChain, StationProfile

__all__ = [
    "RunConfig",
    "profile_for_station",
    "seed_for_pass",
    "seed_for_station",
    "state_dir_for_station",
]

OPERATOR = "Meridian simulator"
"""The operator every virtual station declares.

One name for all of them, and an obviously non-human one. ``operator`` is what a
dashboard shows beside a station, and a simulated fleet wearing invented
operator names would be a fleet somebody has to be told is not real.
"""

VHF_RANGE_HZ = (136_000_000, 138_000_000)
"""The declared tunable range of every virtual station's receive chain.

The whole 2 MHz of the 137 MHz satellite downlink band, so a virtual station can
hear anything a catalogue puts there — including a transmitter added after the
fleet registered, which a narrower range would silently exclude.
"""

MODES = ("lrpt",)
"""What a virtual station declares it can demodulate.

Meteor-M LRPT is this project's primary reception target. A chain tuned to the
right frequency running the wrong demodulator receives nothing, so the mode is
matched as strictly as the frequency and this has to name what the catalogue
carries.
"""

MIN_ELEVATION_FLOOR_DEG = 5.0
MIN_ELEVATION_CEILING_DEG = 15.0
"""The range a station's declared elevation floor is drawn from.

Straddles the 10° at which decode success collapses through atmosphere and local
obstruction — the default floor stations declare in the observation archive. It
is drawn per station rather than fixed because a fleet that all refuse the same
passes is one station tested many times.
"""

LATITUDE_LIMIT_DEG = 70.0
"""How far from the equator a virtual station is placed.

Stops short of the poles, where a sun-synchronous satellite passes on almost
every orbit: a fleet clustered there would see far more work than any real
network and would flatter the scheduler.
"""

MAX_ALTITUDE_M = 1500.0
"""The highest a virtual station is placed.

Altitude materially affects pass geometry at low elevations, so it is varied
rather than defaulted — a fleet all at sea level would never exercise the
difference.
"""

SEED_BITS = 63
"""Width of every seed this module derives.

A station reports its seed at registration (MSP §5) and the platform stores it
in ``stations.seed``, which is a ``bigint`` — signed, so 63 bits is the whole of
what can be recorded. A full 64-bit draw overflows it for roughly half of all
stations, which is a failure that waits until the fleet is large enough to hit
one and then refuses a registration for a reason nothing about seeds explains.

Both derivations are narrowed the same way rather than only the one that crosses
the wire: two rules for one thing is how the second one gets forgotten.
"""

_SEED_MASK = (1 << SEED_BITS) - 1

_COORDINATE_DECIMALS = 4
"""Decimal places kept on a generated latitude or longitude.

About eleven metres, which is far finer than pass geometry can tell apart. It is
rounded at all so the registered body is a short, stable string: an unrounded
float would put seventeen digits on the wire and make one station's registration
harder to read than the whole rest of the run.
"""


@dataclass(frozen=True, slots=True)
class RunConfig:
    """One simulator run, as the CLI resolved it.

    Everything reproducible about a run is here. ``master_seed`` and
    ``station_count`` decide who the stations are, ``scenario`` decides what
    happens to them, and ``run_id`` labels the result — it is reported to the
    platform at registration (MSP §5) and is how a later reader tells one run's
    rows from another's.
    """

    master_seed: int
    run_id: str
    station_count: int
    base_url: str
    state_dir: Path

    scenario: str = "clean"
    """Which fault schedule to run under. ``clean`` injects nothing."""


def seed_for_station(master_seed: int, index: int) -> int:
    """The seed everything about station ``index`` is derived from.

    Args:
        master_seed: The run's seed, as the operator gave it.
        index: Which station, counting from one.

    Returns:
        A seed for this station alone, stable for a given master seed and index.

    Raises:
        ValueError: ``index`` is below one. Stations are numbered from one
            because an operator reads "station 3 of 50", and a zero would
            silently become a fourth identity nobody asked for.

    Note:
        Derived from the index rather than the ``station_id`` the roadmap's
        formula names, because that id does not exist yet — the platform mints
        it during registration, and the profile being registered is generated
        from this seed (D-075).

        SHA-256 rather than :func:`hash`, whose result is salted per process and
        would give a different fleet on every start.
    """
    if index < 1:
        raise ValueError(f"station index counts from one, got {index}")
    return _seed_from(f"{master_seed}:{index}")


def seed_for_pass(station_seed: int, assignment_id: str) -> int:
    """The seed deciding what one station heard on one assignment.

    Args:
        station_seed: The station's own seed, from :func:`seed_for_station`.
        assignment_id: The assignment being executed, as the platform minted it.

    Returns:
        A seed for this station-and-pass pair alone.

    Note:
        Beside :func:`seed_for_station` rather than in
        :mod:`~meridian_sim.outcomes`, so that everything in this run derived
        from the master seed is derived in one file. A reader asking "where does
        randomness enter?" should find the whole answer without following it.

        Keyed on the assignment rather than on a counter, so a station that
        restarts mid-run and executes the same pass again reaches the same
        conclusion about it. A counter would make the outcome depend on how many
        passes happened to precede it, which a restart changes.
    """
    return _seed_from(f"{station_seed}:{assignment_id}")


def _seed_from(material: str) -> int:
    """One seed, derived from text and narrowed to what a ``bigint`` can hold."""
    digest = hashlib.sha256(material.encode()).digest()
    return int.from_bytes(digest[:8], "big") & _SEED_MASK


def state_dir_for_station(config: RunConfig, index: int) -> Path:
    """Where station ``index`` keeps its credentials, held work and outbox.

    Args:
        config: The run being executed.
        index: Which station, counting from one.

    Returns:
        The station's own directory, which is **not** created here — a caller
        that only wants to know whether a station has registered before should
        be able to ask without bringing it into existence.

    Note:
        Keyed on the index and not on ``run_id`` (D-080). A path carrying a
        per-run identifier could not name state that has to outlive the run, so
        every station would find an empty directory on restart, register again,
        and consume a fresh invite each time.
    """
    return config.state_dir / f"{index:03d}"


def profile_for_station(index: int, seed: int, run_id: str) -> StationProfile:
    """The registration profile station ``index`` presents to the platform.

    Args:
        index: Which station, counting from one. Used for the station's name,
            so an operator reading a dashboard can find it.
        seed: This station's seed, from :func:`seed_for_station`.
        run_id: The run this station belongs to, reported under MSP §5.

    Returns:
        A profile declaring itself simulated, with a site and an elevation floor
        drawn from ``seed`` and a receive chain covering the 137 MHz band.

    Note:
        **It declares itself simulated at registration and never again.** The
        platform takes that as authoritative from then on and stamps every
        derived record with it, so no later payload can launder simulated output
        into the measured results (MSP §5, D-048).

        The site may fall in an ocean. A virtual station has no site to be
        wrong about — what has to be real is the geometry, and a pass computed
        over open water is exactly as real as one computed over land.
    """
    # Mersenne Twister, not `secrets`: this draws a fixture, not a credential,
    # and the property wanted is exactly the one a cryptographic source refuses
    # to give — the same seed reproducing the same station on every run.
    stream = random.Random(seed)
    return StationProfile(
        name=f"sim-{index:03d}",
        operator=OPERATOR,
        lat_deg=_coordinate(stream, LATITUDE_LIMIT_DEG),
        lon_deg=_coordinate(stream, 180.0),
        alt_m=round(stream.uniform(0.0, MAX_ALTITUDE_M), 1),
        capabilities=(_receive_chain(stream),),
        simulated=True,
        simulator_run_id=run_id,
        seed=seed,
    )


def _receive_chain(stream: random.Random) -> ReceiveChain:
    """The one antenna and receiver a virtual station declares.

    One chain rather than several: a second would be hardware nothing in the
    catalogue transmits to, and a declared capability that can never match is a
    fixture pretending to be a feature.
    """
    return ReceiveChain(
        band="vhf",
        freq_min_hz=VHF_RANGE_HZ[0],
        freq_max_hz=VHF_RANGE_HZ[1],
        modes=MODES,
        polarisation="rhcp",
        tracking=True,
        min_elevation_deg=round(
            stream.uniform(MIN_ELEVATION_FLOOR_DEG, MIN_ELEVATION_CEILING_DEG), 1
        ),
    )


def _coordinate(stream: random.Random, limit_deg: float) -> float:
    """One latitude or longitude, drawn symmetrically about zero and rounded."""
    return round(stream.uniform(-limit_deg, limit_deg), _COORDINATE_DECIMALS)

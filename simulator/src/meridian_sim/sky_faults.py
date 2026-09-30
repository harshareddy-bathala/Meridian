"""Stage 25's four ground-truth faults: what each is, and when, from a seed.

Each fault's vocabulary entry is :mod:`~meridian_sim.faults`'. This module holds
their *shapes* — the rate, the sector, the hours, the satellite — and draws them,
with their onsets, from a seed, so two runs at one seed break the same way in the
same place. What each does to a pass's evidence is
:mod:`~meridian_sim.sky_effects`'.

**Onset is a tick, as for every other fault**, and the instant the fault opened
is what its effect is measured from: a degradation's loss grows from the true
time it came into force, which the supervisor records and the ledger writes.

**Persistent, unlike Stage 21's.** A degradation, a new obstruction and an
interference source do not recur on a cycle; they arrive and stay, which is what
makes them causes a diagnosis can find from a station's history. Only a silent
satellite ends, because a transmitter switched off is switched back on.

Every parameter a fault was drawn with goes to the ledger's ``open`` line, beside
the seed, and nowhere else: never an MSP body, never a platform table (D-105).

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 25; docs/DECISIONS.md
D-105, D-189, D-253; docs/SCALE-AND-FAULTS.md § Ground-truth faults.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime

from meridian_sim.faults import (
    INTERFERENCE,
    OBSTRUCTION,
    SATELLITE_SILENT,
    SCENARIOS,
    SIGNAL_DEGRADATION,
)
from meridian_sim.sky_track import SkyPoint

__all__ = [
    "ActiveSkyFault",
    "Degradation",
    "Interference",
    "Obstruction",
    "Silence",
    "SkyFault",
    "SkyInForce",
    "in_sector",
    "silence_for",
    "sky_faults_for",
]

ONSET_TICK_RANGE = (5, 60)
"""When a station's sky fault comes into force, in ticks from the run's start.

Late enough that the station has reported clean passes first — a baseline is
what a degradation or a raised floor is a change *from* — and early enough that
a short run sees the fault.
"""

DEGRADATION_RATE_DB_PER_DAY = (1.0, 8.0)
"""How fast a degrading chain loses signal. A slow connector to a failing LNA."""

OBSTRUCTION_WIDTH_DEG = (30.0, 90.0)
OBSTRUCTION_BELOW_DEG = (15.0, 35.0)
"""An obstruction's sector width, and the elevation it blocks up to."""

INTERFERENCE_WIDTH_DEG = (45.0, 120.0)
INTERFERENCE_HOURS = (2, 6)
INTERFERENCE_RISE_DB = (6.0, 15.0)
"""An interference source's sector, its daily hours, and how far it lifts the floor."""

SILENCE_ONSET_TICK_RANGE = (10, 60)
SILENCE_DURATION_TICKS = (20, 120)
"""When a satellite falls silent across the fleet, and for how long, in ticks."""


@dataclass(frozen=True, slots=True)
class Degradation:
    """A receive-chain loss growing at ``rate_db_per_day`` from the onset."""

    rate_db_per_day: float

    def loss_db(self, onset_at: datetime, at: datetime) -> float:
        """The loss at ``at``; none before the onset."""
        return max(0.0, (at - onset_at).total_seconds() / 86_400) * self.rate_db_per_day


@dataclass(frozen=True, slots=True)
class Obstruction:
    """A sector blocked below an elevation. Never declared in ``horizon_mask``."""

    azimuth_from_deg: float
    width_deg: float
    below_elevation_deg: float

    def blocks(self, point: SkyPoint) -> bool:
        """Whether a sample from this direction is blocked."""
        return 0.0 <= point.elevation_deg < self.below_elevation_deg and in_sector(
            point.azimuth_deg, self.azimuth_from_deg, self.width_deg
        )


@dataclass(frozen=True, slots=True)
class Interference:
    """A raised floor in a sector, for ``hours`` from ``start_hour_utc`` daily."""

    azimuth_from_deg: float
    width_deg: float
    start_hour_utc: int
    hours: int
    rise_db: float

    def raises(self, point: SkyPoint, at: datetime) -> bool:
        """Whether a sample from this direction, at this instant, is raised."""
        in_hours = (at.hour - self.start_hour_utc) % 24 < self.hours
        return in_hours and in_sector(
            point.azimuth_deg, self.azimuth_from_deg, self.width_deg
        )


@dataclass(frozen=True, slots=True)
class Silence:
    """One satellite's transmitter off, at every station."""

    satellite_id: str


Shape = Degradation | Obstruction | Interference | Silence


@dataclass(frozen=True, slots=True)
class SkyFault:
    """One sky fault: its kind, its shape, and the ticks it holds over."""

    kind: str
    shape: Shape
    first_tick: int
    last_tick: int | None = None
    """``None`` for a fault that never ends, which is all but a silence."""

    def active_at(self, tick: int) -> bool:
        """Whether this fault is in force on ``tick``."""
        return tick >= self.first_tick and (
            self.last_tick is None or tick <= self.last_tick
        )

    def detail(self) -> dict[str, object]:
        """The parameters the ledger records beside the seed when it opens."""
        return dict(asdict(self.shape))


@dataclass(frozen=True, slots=True)
class ActiveSkyFault:
    """A sky fault in force, with the true instant it came into force."""

    fault: SkyFault
    onset_at: datetime


@dataclass
class SkyInForce:
    """Which sky faults are in force on one station, and since when.

    The supervisor's record, kept across a station's restarts: a degradation's
    loss is measured from the instant it came into force, and a process that
    restarted mid-fault has not repaired the cable.
    """

    onsets: dict[str, datetime] = field(default_factory=dict)

    def update(
        self, faults: Iterable[SkyFault], active: frozenset[str], now: datetime
    ) -> tuple[ActiveSkyFault, ...]:
        """The faults in force this round, each with its onset.

        A fault seen in force for the first time came into force ``now``; one
        no longer in force is forgotten, so a silence that returns later has
        a new onset of its own.
        """
        for kind in [one for one in self.onsets if one not in active]:
            del self.onsets[kind]
        return tuple(
            ActiveSkyFault(one, self.onsets.setdefault(one.kind, now))
            for one in faults
            if one.kind in active
        )


def sky_faults_for(station_seed: int, scenario: str) -> tuple[SkyFault, ...]:
    """The station's own sky faults — every one but a silence, which is the fleet's.

    Args:
        station_seed: The station's seed.
        scenario: A key of :data:`~meridian_sim.faults.SCENARIOS`.

    Returns:
        One fault per kind the scenario names, each on a stream of its own —
        seeded on the station, the scenario and the kind, as Stage 21's are — so
        adding a kind moves nothing already drawn.

    Raises:
        KeyError: No such scenario.
    """
    kinds = SCENARIOS[scenario]
    drawn: list[SkyFault] = []
    for kind in (SIGNAL_DEGRADATION, OBSTRUCTION, INTERFERENCE):
        if kind not in kinds:
            continue
        stream = random.Random(f"{station_seed}:{scenario}:{kind}")
        onset = stream.randint(*ONSET_TICK_RANGE)
        drawn.append(SkyFault(kind, _shape(kind, stream), onset))
    return tuple(drawn)


def silence_for(
    master_seed: int, scenario: str, satellite_id: str | None
) -> SkyFault | None:
    """The fleet's silent satellite, if the scenario has one.

    Args:
        master_seed: The run's seed.
        scenario: A key of :data:`~meridian_sim.faults.SCENARIOS`.
        satellite_id: Which satellite falls silent. Named by the operator: the
            simulator never sees the catalogue, so it cannot draw one.

    Raises:
        KeyError: No such scenario.
        ValueError: The scenario silences a satellite and none was named.
            Refused rather than skipped, because a ``silent`` run that silenced
            nothing would look exactly like a run where nothing went wrong.
    """
    if SATELLITE_SILENT not in SCENARIOS[scenario]:
        return None
    if not satellite_id:
        raise ValueError(f"scenario {scenario!r} needs the satellite to silence")
    stream = random.Random(f"{master_seed}:{scenario}:{SATELLITE_SILENT}")
    first = stream.randint(*SILENCE_ONSET_TICK_RANGE)
    duration = stream.randint(*SILENCE_DURATION_TICKS)
    return SkyFault(
        SATELLITE_SILENT, Silence(satellite_id), first, first + duration - 1
    )


def in_sector(azimuth_deg: float, from_deg: float, width_deg: float) -> bool:
    """Whether an azimuth lies in the sector running clockwise from ``from_deg``."""
    return (azimuth_deg - from_deg) % 360.0 < width_deg


def _shape(kind: str, stream: random.Random) -> Shape:
    """One fault's parameters, drawn in a fixed order from its own stream."""
    if kind == SIGNAL_DEGRADATION:
        return Degradation(round(stream.uniform(*DEGRADATION_RATE_DB_PER_DAY), 2))
    if kind == OBSTRUCTION:
        return Obstruction(
            azimuth_from_deg=round(stream.uniform(0.0, 360.0), 1),
            width_deg=round(stream.uniform(*OBSTRUCTION_WIDTH_DEG), 1),
            below_elevation_deg=round(stream.uniform(*OBSTRUCTION_BELOW_DEG), 1),
        )
    return Interference(
        azimuth_from_deg=round(stream.uniform(0.0, 360.0), 1),
        width_deg=round(stream.uniform(*INTERFERENCE_WIDTH_DEG), 1),
        start_hour_utc=stream.randint(0, 23),
        hours=stream.randint(*INTERFERENCE_HOURS),
        rise_db=round(stream.uniform(*INTERFERENCE_RISE_DB), 1),
    )

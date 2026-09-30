"""The faults a fleet suffers together: a partition, and a silent satellite.

Every other fault is one station's, drawn from its own seed. These two are drawn
once for the run and opened on each station they reach — a partition on its
members, a silence on every station — so the ledger keeps one target per
station and a reader never meets a target that is not a station or the
platform. Pure, like the schedules they sit beside.

Reference: docs/DECISIONS.md D-188, D-189, D-253.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian_sim.config import RunConfig
from meridian_sim.fault_schedule import FleetPartition, partition_for
from meridian_sim.faults import PARTITION
from meridian_sim.sky_faults import SkyFault, silence_for

__all__ = ["FleetFaults", "fleet_faults_for"]


@dataclass(frozen=True, slots=True)
class FleetFaults:
    """What a whole fleet will suffer, and when."""

    partition: FleetPartition = field(default_factory=FleetPartition)
    silence: SkyFault | None = None

    @property
    def sky(self) -> tuple[SkyFault, ...]:
        """The fleet's sky faults, for a station's own to be joined with."""
        return () if self.silence is None else (self.silence,)

    def active_for(self, index: int, tick: int) -> frozenset[str]:
        """Which fleet faults reach station ``index`` on ``tick``."""
        active: set[str] = set()
        if self.partition.active_for(index, tick):
            active.add(PARTITION)
        if self.silence is not None and self.silence.active_at(tick):
            active.add(self.silence.kind)
        return frozenset(active)


def fleet_faults_for(config: RunConfig) -> FleetFaults:
    """Draw the fleet's faults for a run.

    Raises:
        KeyError: No such scenario.
        ValueError: The scenario silences a satellite and the run named none.
    """
    return FleetFaults(
        partition=partition_for(
            config.master_seed, config.scenario, config.station_count
        ),
        silence=silence_for(
            config.master_seed, config.scenario, config.silent_satellite
        ),
    )

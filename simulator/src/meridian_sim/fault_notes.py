"""Writing a fleet's faults to the run's ledger as they open, close and act.

The supervisor knows what is scheduled to break and which station it broke; this
turns each round's difference into ledger lines (D-189). Kept apart from the
supervisor's own work — running the fleet — so each reads on its own.

Reference: docs/DECISIONS.md D-188, D-189.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from meridian_sim.fault_schedule import FaultSchedule
from meridian_sim.faults import CLOCK_DRIFT, PARTITION, SATELLITE_SILENT
from meridian_sim.fleet_faults import FleetFaults
from meridian_sim.ledger import FaultLedger

__all__ = ["FaultNotes", "target_of"]


def target_of(index: int) -> str:
    """How the ledger names station ``index``."""
    return f"station:{index}"


@dataclass
class FaultNotes:
    """The ledger a fleet writes to, or none, and the faults it suffers together."""

    ledger: FaultLedger | None
    fleet: FleetFaults = field(default_factory=FleetFaults)

    def transitions(  # noqa: PLR0913 — one station's round, named at the call
        self,
        *,
        index: int,
        station: tuple[str, int],
        schedule: FaultSchedule,
        was: frozenset[str],
        now_active: frozenset[str],
        tick: int,
        now: datetime,
    ) -> None:
        """Write each fault that opened or closed on this round.

        ``station`` is its id and seed, stamped on every ``open``.
        """
        if self.ledger is None:
            return
        target = target_of(index)
        station_id, seed = station
        for kind in sorted(now_active - was):
            self.ledger.open(
                kind,
                target,
                now,
                tick=tick,
                station_id=station_id,
                seed=seed,
                detail=self._detail(schedule, kind),
            )
        for kind in sorted(was - now_active):
            self.ledger.close(kind, target, now, tick=tick)

    def acts(
        self,
        index: int,
        acts: Iterable[tuple[str, str]],
        tick: int,
        now: datetime,
    ) -> None:
        """Write what open faults did to named assignments, one line a kind."""
        by_kind: defaultdict[str, list[str]] = defaultdict(list)
        for kind, assignment_id in acts:
            by_kind[kind].append(assignment_id)
        if self.ledger is None:
            return
        for kind, ids in sorted(by_kind.items()):
            self.ledger.act(kind, target_of(index), now, tuple(ids), tick=tick)

    def _detail(self, schedule: FaultSchedule, kind: str) -> dict[str, object]:
        """What a reader of the ledger needs to know about one fault's shape."""
        if kind == CLOCK_DRIFT:
            return {"drift_s_per_tick": schedule.drift_s_per_tick}
        if kind == PARTITION:
            return {"members": sorted(self.fleet.partition.members)}
        if kind == SATELLITE_SILENT and self.fleet.silence is not None:
            # The fleet's fault, opened on each station it reaches: the detail
            # says so, so a reader counts one cause and not one per station.
            return {**self.fleet.silence.detail(), "fleet_wide": True}
        sky = {one.kind: one.detail() for one in schedule.sky}
        return sky.get(kind, {})

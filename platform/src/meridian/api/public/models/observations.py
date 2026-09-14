"""Observations and simulator runs, as the public API states them.

An observation's window is widened to the minute on the way here (D-093). What it
does not carry — detection instant, Doppler curve, notes, products — was never
selected by ``store.observation_history``, so there is nothing to drop.

A simulator run carries ``simulated: true`` as a constant. It is still a field:
every applicable body states its provenance (CLAUDE.md rule 5), and a run is
never anything else.

Reference: docs/DECISIONS.md D-015, D-077, D-093.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.api.public.window_privacy import publish_window
from meridian.store.observation_history import HistoricObservation
from meridian.store.simulator_runs import SimulatorRun

__all__ = ["PublicObservation", "PublicSimulatorRun"]


class PublicObservation(BaseModel):
    """What one station reported about one pass, as last corrected."""

    observation_id: str
    assignment_id: str
    revision: int
    """How many times the report was corrected, counting from 1 (D-015)."""
    station_id: str
    satellite_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    provenance: str
    submitted_at: datetime
    simulated: bool

    @classmethod
    def from_row(cls, row: HistoricObservation) -> Self:
        """Publish one observation with its window widened."""
        window = publish_window(row.started_at, row.ended_at)
        return cls(
            observation_id=row.observation_id,
            assignment_id=row.assignment_id,
            revision=row.revision,
            station_id=row.station_id,
            satellite_id=row.satellite_id,
            started_at=window.start,
            ended_at=window.end,
            outcome=row.outcome,
            signal_detected=row.signal_detected,
            peak_snr_db=row.peak_snr_db,
            provenance=row.provenance,
            submitted_at=row.submitted_at,
            simulated=row.simulated,
        )


class PublicSimulatorRun(BaseModel):
    """The virtual stations one simulator run registered. Never its seed."""

    run_id: str
    station_count: int
    first_registered_at: datetime
    last_registered_at: datetime
    last_heartbeat_at: datetime | None
    simulated: Literal[True] = True

    @classmethod
    def from_row(cls, row: SimulatorRun) -> Self:
        """Publish one grouped run."""
        return cls(
            run_id=row.run_id,
            station_count=row.station_count,
            first_registered_at=row.first_registered_at,
            last_registered_at=row.last_registered_at,
            last_heartbeat_at=row.last_heartbeat_at,
        )

"""What just happened across the network, as the public API states it.

Each event is a record the platform already publishes elsewhere, read back
with the time it happened (D-363). So an event discloses nothing its own
endpoint does not: a station's registration time (``/stations``), a report's
arrival (``/observations``), a diagnosis's computation (``/losses``) and a
warning's raising and clearing (``/receive-chain-warnings``). No pass window is
published here at all, so D-093's widened windows are not undone.

Every event carries ``simulated``, from the record it came from (rule 5).

Reference: docs/DECISIONS.md D-093, D-210, D-363.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.store.event_reads import ListedEvent

__all__ = ["EventKind", "PublicEvent"]

EventKind = Literal[
    "loss_diagnosed",
    "pass_decoded",
    "station_joined",
    "warning_cleared",
    "warning_raised",
]


class PublicEvent(BaseModel):
    """One thing that happened, and the record that says so."""

    at: datetime
    """When it happened: the registration, the report's arrival, the diagnosis,
    the raising or the clearing."""

    kind: EventKind
    ref: str
    """The record it came from, by kind: the station's id, the observation's,
    the diagnosis's, or the warning's. Each is the id that record's own endpoint
    publishes."""

    station_id: str
    simulated: bool
    satellite_id: str | None
    """For a decode and a loss; ``null`` for the other kinds, which concern a
    station rather than a pass."""

    assignment_id: str | None
    """For a decode and a loss, the work it was; ``null`` otherwise."""

    cause: str | None
    """For a loss, its cause or ``undetermined`` (D-273); ``null`` otherwise."""

    @classmethod
    def from_row(cls, row: ListedEvent) -> Self:
        """Publish one event."""
        return cls.model_validate(
            {
                "at": row.at,
                "kind": row.kind,
                "ref": row.ref,
                "station_id": row.station_id,
                "simulated": row.simulated,
                "satellite_id": row.satellite_id,
                "assignment_id": row.assignment_id,
                "cause": row.cause,
            }
        )

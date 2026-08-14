"""A station's heartbeats, as the public API states them.

Turns a ``store.heartbeats.RecordedHeartbeat`` into a response body. Nothing is
coarsened here — unlike a station's coordinates, a heartbeat is the platform's
own record of a report it received, and there is no operator preference over it.

**What a heartbeat does not carry is decided in the store, not here.** The read
this model is built from never selects ``health_json``, so there is no field to
forget to drop. That is deliberate: a filter applied at serialisation is one
`model_dump` away from being bypassed, while a column that was never selected
cannot be published by accident.

The listening block travels as a nested object rather than four flat fields, so
"the station was listening" and "it was not" are one presence check instead of
four — which is the check the whole confirmed-miss argument rests on.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-013, D-085.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.store.heartbeats import RecordedHeartbeat

__all__ = ["ListeningBlock", "PublicHeartbeat"]


class ListeningBlock(BaseModel):
    """What a station reported it was tuned to when it sent a heartbeat.

    Present only when the station was listening. Its absence is not "no data" —
    it is the station saying it was doing something else, which is a different
    claim and the reason a missed pass can be told from an idle one.
    """

    assignment_id: str
    satellite_id: str
    centre_freq_hz: int
    mode: str


def _listening_block(row: RecordedHeartbeat) -> ListeningBlock | None:
    """Gather the four listening columns, or ``None`` if the station was not.

    All four are checked rather than one standing in for the rest. The database
    guarantees they move together — ``heartbeat_listening_complete`` says so —
    but reading one and coercing the others would turn a violated constraint into
    a heartbeat published as *listening at 0 Hz*, which is a fabricated
    measurement rather than a missing one. Checking all four makes the type
    honest and costs nothing.
    """
    if (
        row.listening_assignment_id is None
        or row.listening_satellite_id is None
        or row.listening_freq_hz is None
        or row.listening_mode is None
    ):
        return None

    return ListeningBlock(
        assignment_id=row.listening_assignment_id,
        satellite_id=row.listening_satellite_id,
        centre_freq_hz=row.listening_freq_hz,
        mode=row.listening_mode,
    )


class PublicHeartbeat(BaseModel):
    """One heartbeat, as a reader receives it."""

    id: int
    station_id: str

    sent_at: datetime
    """The station's own clock, which is untrusted (D-013)."""

    received_at: datetime
    """The platform's clock. Published beside ``sent_at`` rather than instead of
    it: the difference between the two is how a station with a failing RTC is
    spotted, and one timestamp alone hides that."""

    state: str
    """What the station reported it was doing — never the platform's conclusion
    about whether it is alive. That is ``liveness``, and D-013 keeps the two
    words apart on purpose."""

    held_assignments: list[str]
    listening: ListeningBlock | None
    clock_offset_s: float | None
    clock_uncertainty_s: float | None
    """Both nullable, and ``null`` means unknown rather than zero. A station
    claiming a perfect clock and one that cannot measure its own are opposite
    cases, and averaging them together would be a fabricated number."""

    simulated: bool

    @classmethod
    def from_row(cls, row: RecordedHeartbeat) -> Self:
        """Build the response body for one stored heartbeat.

        Args:
            row: The heartbeat as the store returned it, already without
                ``health_json``.

        Returns:
            The heartbeat, with its four listening columns gathered into one
            object or into ``None``.
        """
        return cls(
            id=row.id,
            station_id=row.station_id,
            sent_at=row.sent_at,
            received_at=row.received_at,
            state=row.state,
            held_assignments=list(row.held_assignments),
            listening=_listening_block(row),
            clock_offset_s=row.clock_offset_s,
            clock_uncertainty_s=row.clock_uncertainty_s,
            simulated=row.simulated,
        )

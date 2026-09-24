"""What a station's own record said before a pass began — D-157.

A history feature is only honest if the model could have known it when the
pass was scheduled. So every outcome here is an **event with a settled time**:
the pass's ``los`` plus the labelling ``settle_margin_s`` (D-146), the moment
its label stopped being able to change. A query for a pass returns only the
events settled at or before that pass's ``aos``; the pass's own outcome, and
anything after it, cannot be reached through this module.

**Which passes become events.** Measured passes with a label — scheduled, and
settled by export. A simulated pass is never one (D-078); an unscheduled pass
has no outcome to remember. From each event two facts are kept:

* **available** — the station took the pass up: it was not declined, not
  unavailable, and not unconfirmed as listening. The station-health feature
  counts these (``EVALUATION.md`` §2: "recent failure rate at this station");
* **usable and success** — the label is a yield label (``USABLE_LABELS``), and
  it is ``successful_reception``. The decode-rate features count these.

Pure: labels in, answers out. No clock — "now" is always a pass's ``aos``.

Reference: docs/DECISIONS.md D-078, D-146, D-149, D-157.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.datasets.completeness import USABLE_LABELS
from meridian.datasets.labels import LabelledPass

__all__ = [
    "RECENT",
    "UNAVAILABLE_LABELS",
    "Event",
    "History",
    "Rate",
    "events_of",
]

UNAVAILABLE_LABELS = frozenset(
    ("station_unavailable", "station_not_confirmed_listening", "assignment_declined")
)
"""A scheduled pass the station did not take up (D-146 rules 3, 6, 7 and 8)."""

_SUCCESS = "successful_reception"

RECENT = 20
"""How many of a station's latest outcomes its recent rates are taken over."""


@dataclass(frozen=True, slots=True)
class Event:
    """One settled outcome at one station."""

    station_id: str
    satellite_id: str
    band: str
    settled_at: datetime
    available: bool
    usable: bool
    success: bool


@dataclass(frozen=True, slots=True)
class Rate:
    """Successes out of trials, and the rate shrunk towards one half.

    ``(successes + 1) / (trials + 2)`` — Laplace's rule — so a station with
    no history reads 0.5, never NaN, and one success in one trial reads 2/3,
    not certainty. ``trials`` travels beside it so a model can learn how far
    to trust it (D-159, D-161).
    """

    successes: int
    trials: int

    @property
    def smoothed(self) -> float:
        """The rate, shrunk towards one half by two pseudo-trials."""
        return (self.successes + 1) / (self.trials + 2)


def events_of(
    labelled: Iterable[LabelledPass],
    *,
    bands: Mapping[str, str],
    settle_margin_s: int,
) -> tuple[Event, ...]:
    """Every measured, scheduled, settled pass as an event.

    Args:
        labelled: The evaluation dataset's labelled passes.
        bands: Each satellite's band; a satellite with none is ``"unknown"``.
        settle_margin_s: The labelling configuration's margin (D-146).

    Returns:
        The events, in no particular order.
    """
    margin = timedelta(seconds=settle_margin_s)
    return tuple(
        Event(
            station_id=one.station_id,
            satellite_id=one.satellite_id,
            band=bands.get(one.satellite_id, "unknown"),
            settled_at=one.los + margin,
            available=one.label not in UNAVAILABLE_LABELS,
            usable=one.label in USABLE_LABELS,
            success=one.label == _SUCCESS,
        )
        for one in labelled
        if one.label is not None and not one.simulated
    )


class History:
    """Events indexed by station, station and satellite, and station and band."""

    def __init__(self, events: Iterable[Event]) -> None:
        """Index the events by settled time under each key they answer for."""
        held: dict[tuple[str, ...], list[Event]] = {}
        for one in events:
            for key in (
                ("station", one.station_id),
                ("satellite", one.station_id, one.satellite_id),
                ("band", one.station_id, one.band),
            ):
                held.setdefault(key, []).append(one)
        self._events = {
            key: sorted(value, key=lambda one: one.settled_at)
            for key, value in held.items()
        }
        self._times = {
            key: [one.settled_at for one in value]
            for key, value in self._events.items()
        }

    def before(self, key: tuple[str, ...], at: datetime) -> Sequence[Event]:
        """Events under ``key`` settled at or before ``at``, oldest first."""
        times = self._times.get(key)
        if times is None:
            return ()
        return self._events[key][: bisect_right(times, at)]

    def decode_rate(
        self, key: tuple[str, ...], at: datetime, *, recent: int | None = None
    ) -> Rate:
        """Successes among usable outcomes before ``at``; the last ``recent`` only."""
        usable = [one for one in self.before(key, at) if one.usable]
        if recent is not None:
            usable = usable[-recent:]
        return Rate(sum(one.success for one in usable), len(usable))

    def availability(self, station_id: str, at: datetime, *, recent: int) -> Rate:
        """How many of the station's last ``recent`` scheduled passes it took up."""
        taken = list(self.before(("station", station_id), at))[-recent:]
        return Rate(sum(one.available for one in taken), len(taken))

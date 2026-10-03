"""Other receptions of a satellite near a pass: the evidence D-147 counts.

Shared by the classification (:mod:`~meridian.reliability.accounting`) and the
loss diagnosis (:mod:`~meridian.reliability.diagnosis_gather`), so the two read
the satellite from one definition of which receptions count:

* **only the pass's own population** — a simulated reception is never evidence
  about a measured pass, nor the other way round;
* **each other physical pass once**, by its most informative report, as the
  snapshot labeller counts it;
* **a silence only on the registry's word** that the station was listening for
  one of its assignments;
* **our own receptions only** (D-182). The diagnosis asks, besides, for
  receptions a station reported, never an archive's or a hand-entered one
  (D-102, D-108); the classification's answer is unchanged.

Moved here from :mod:`~meridian.reliability.accounting` by Stage 27 without a
change of rule, so the classification's method stays ``classification-1``.

Reference: docs/DECISIONS.md D-146, D-147, D-182, D-276.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from typing import Protocol, TypeVar

from meridian.registry import ListeningQuery, Registry
from meridian.reliability.classification import OUTCOME_ORDER
from meridian.reliability.satellite_silence import SIGNAL
from meridian.store.reliability_evidence import find_receptions_near
from meridian.store.stations import Connection

__all__ = ["informativeness", "overlapping", "question", "satellite_evidence"]


class _Windowed(Protocol):
    @property
    def assignment_id(self) -> str: ...
    @property
    def station_id(self) -> str: ...
    @property
    def satellite_id(self) -> str: ...
    @property
    def start_at(self) -> datetime: ...
    @property
    def end_at(self) -> datetime: ...


W = TypeVar("W", bound=_Windowed)


class _Asked(_Windowed, Protocol):
    @property
    def centre_freq_hz(self) -> int: ...
    @property
    def mode(self) -> str: ...


def satellite_evidence(  # noqa: PLR0913 — one question's terms, each by name
    conn: Connection,
    registry: Registry,
    *,
    satellite_id: str,
    between: tuple[datetime, datetime],
    excluding: Sequence[str],
    simulated: bool,
    station_reported: bool = False,
    silent_min_elevation_deg: float = 0.0,
) -> tuple[list[str], list[str]]:
    """Other receptions of the satellite in ``between``, as D-147 counts them.

    Args:
        conn: An open connection, on which ``registry`` is also bound.
        registry: Answers whether each silent station was listening.
        satellite_id: The satellite.
        between: The interval another pass must begin and end in.
        excluding: The assignments of the pass being judged.
        simulated: The pass's population; only receptions of it count.
        station_reported: Count only receptions a station reported, leaving out
            an archive's or a hand-entered one. The diagnosis asks this.
        silent_min_elevation_deg: Count a silence only from a pass that
            climbed this high, where hearing nothing says something. A signal
            counts at any height. The diagnosis asks this (D-276).

    Returns:
        The reporting assignment of each physical pass that heard a signal, and
        of each that heard nothing while confirmed listening.
    """
    nearby = [
        reception
        for reception in find_receptions_near(
            conn,
            satellite_id=satellite_id,
            between=between,
            excluding=excluding,
            station_reported=station_reported,
        )
        if reception.simulated == simulated
    ]
    signals: list[str] = []
    silences: list[str] = []
    for physical in overlapping(nearby):
        best = min(
            physical,
            key=lambda held: informativeness(held.outcome, held.assignment_id),
        )
        if best.outcome in SIGNAL:
            signals.append(best.assignment_id)
        elif (
            best.outcome == "no_signal"
            and max(held.max_elevation_deg for held in physical)
            >= silent_min_elevation_deg
            and any(registry.was_listening(question(held)) for held in physical)
        ):
            silences.append(best.assignment_id)
    return sorted(signals), sorted(silences)


def overlapping(held: Iterable[W]) -> Iterator[tuple[W, ...]]:
    """Assignments of one station and satellite whose windows overlap, grouped.

    Ordered by station, satellite and start, a group is extended while the next
    assignment begins before the group's end.
    """
    group: list[W] = []
    for one in sorted(
        held,
        key=lambda one: (
            one.station_id,
            one.satellite_id,
            one.start_at,
            one.assignment_id,
        ),
    ):
        if group and (
            (one.station_id, one.satellite_id)
            != (group[0].station_id, group[0].satellite_id)
            or one.start_at >= max(held.end_at for held in group)
        ):
            yield tuple(group)
            group = []
        group.append(one)
    if group:
        yield tuple(group)


def question(held: _Asked) -> ListeningQuery:
    """The listening question for one assignment, on the assignment's own terms."""
    return ListeningQuery(
        station_id=held.station_id,
        satellite_id=held.satellite_id,
        centre_freq_hz=held.centre_freq_hz,
        mode=held.mode,
        window=(held.start_at, held.end_at),
    )


def informativeness(outcome: str, assignment_id: str) -> tuple[int, str]:
    """Rank a report by :data:`OUTCOME_ORDER`, then by id, as the labeller does."""
    rank = (
        OUTCOME_ORDER.index(outcome) if outcome in OUTCOME_ORDER else len(OUTCOME_ORDER)
    )
    return rank, assignment_id

"""Which computed pass each archive reception belongs to — D-150.

An archive station's reception names a satellite and a start time, and nothing
else about the pass. It is placed on the computed pass (``archive_passes``) of
the same station and satellite whose window, widened by the configured
tolerance, holds its start; the nearest acquisition wins when two do. A
reception no computed pass holds is counted, never dropped: it is a pass the
denominator could not place.

Matched once per labelling run, and read by both completeness and the
propensity, so the two cannot disagree about what the archive attempted.

Reference: docs/DECISIONS.md D-150, D-153.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from meridian.datasets.snapshot_rows import ArchivePassRow, ArchiveReception

__all__ = ["ReceptionMatches", "match_receptions"]


@dataclass(frozen=True, slots=True)
class ReceptionMatches:
    """Which passes the receptions claim, and the days each station was heard."""

    heard: dict[int, set[date]]
    attempted: frozenset[ArchivePassRow]
    usable: frozenset[ArchivePassRow]
    succeeded: frozenset[ArchivePassRow]
    """Matched by a ``decoded`` reception: an archive success (D-153)."""

    unmatched: int


def match_receptions(
    passes: Sequence[ArchivePassRow],
    receptions: Iterable[ArchiveReception],
    tolerance_s: int,
) -> ReceptionMatches:
    """Place each reception on the computed pass it belongs to, if any."""
    by_pair: dict[tuple[int, str], list[ArchivePassRow]] = {}
    for candidate in passes:
        pair = (candidate.archive_station_id, candidate.satellite_id)
        by_pair.setdefault(pair, []).append(candidate)
    placed = {candidate.archive_station_id for candidate in passes}
    heard: dict[int, set[date]] = {}
    attempted: set[ArchivePassRow] = set()
    usable: set[ArchivePassRow] = set()
    succeeded: set[ArchivePassRow] = set()
    unmatched = 0
    for reception in receptions:
        match = _nearest_pass(reception, by_pair, tolerance_s)
        if reception.archive_station_id in placed:
            heard.setdefault(reception.archive_station_id, set()).add(
                reception.started_at.date()
            )
        if match is None:
            unmatched += 1
            continue
        attempted.add(match)
        if reception.archive_outcome != "unknown":
            usable.add(match)
        if reception.archive_outcome == "decoded":
            succeeded.add(match)
    return ReceptionMatches(
        heard=heard,
        attempted=frozenset(attempted),
        usable=frozenset(usable),
        succeeded=frozenset(succeeded),
        unmatched=unmatched,
    )


def _nearest_pass(
    reception: ArchiveReception,
    by_pair: dict[tuple[int, str], list[ArchivePassRow]],
    tolerance_s: int,
) -> ArchivePassRow | None:
    """The computed pass a reception belongs to: nearest acquisition wins."""
    if reception.satellite_key_kind != "norad":
        return None
    widen = timedelta(seconds=tolerance_s)
    candidates = [
        one
        for one in by_pair.get(
            (reception.archive_station_id, reception.satellite_key), []
        )
        if one.aos - widen <= reception.started_at < one.los + widen
    ]
    return min(
        candidates,
        key=lambda one: (abs(one.aos - reception.started_at), one.aos),
        default=None,
    )

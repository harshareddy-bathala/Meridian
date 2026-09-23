"""The passes an archive station could have received, computed by us — D-150.

``EVALUATION.md`` §4.1 divides what a station observed by what was
geometrically available to it, and D-138 keeps that denominator ours: no
archive's own count of its opportunities is trusted. So the export propagates
each archive station's published location with our element sets, and freezes
the answer as ``archive_passes.jsonl``. Labelling then counts against a file,
and its hash never rests on floating-point propagation agreeing across
machines.

**What is computed, per station:**

* its days run from the UTC date of its first reception in scope to that of
  its last; a day outside that span is not the station's at all;
* its satellites are those it has a reception of in scope, keyed by NORAD
  number. Archives declare no capability we read, so what a station has shown
  it can receive stands in for what it could — narrower than the truth, which
  makes completeness look better than it is, and D-150 says so;
* each satellite, each day, is propagated with the element set current at the
  start of that UTC day — newest epoch not after it, ties to the latest
  retrieval, then the highest id, as ``find_element_set_current_at`` orders
  them — over that day inside the scope, against the geometric horizon. The
  labelling configuration's elevation floor is applied later, to
  ``max_elevation_deg``, so changing it needs no new export.

**What cannot be computed is counted, not dropped**, each under
``archive_denominator.*`` in the manifest: a station with no location, one
with no altitude (propagated at 0 m), a received satellite not keyed by NORAD
number, one we hold no element set for, and a satellite-day with no set
current at its start.

Pure: rows in, rows and counts out. The orbit service is handed in.

Reference: docs/DECISIONS.md D-138, D-150.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from meridian.orbit.types import ElementSet, GroundSite, PassSearch, PassWindow

__all__ = [
    "ARCHIVE_PASSES",
    "ArchivePasses",
    "ArchiveRows",
    "PassFinder",
    "compute_archive_passes",
]

ARCHIVE_PASSES = "archive_passes"
"""The file is ``archive_passes.jsonl``."""

_DAY = timedelta(days=1)

_COUNTS = (
    "stations_without_location",
    "stations_without_altitude",
    "satellites_not_norad",
    "satellites_without_element_sets",
    "satellite_days_without_element_set",
)


class PassFinder(Protocol):
    """The one part of ``OrbitService`` a denominator needs."""

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        """Every pass rising inside the search's interval."""
        ...


@dataclass(frozen=True, slots=True)
class ArchiveRows:
    """The three tables the denominator is computed from, as the export read them."""

    stations: Sequence[Mapping[str, object]]
    """``archive_stations``."""

    receptions: Sequence[Mapping[str, object]]
    """``archive_observations`` in scope."""

    element_sets: Sequence[Mapping[str, object]]
    """``element_sets``, including those current at each day's start for every
    satellite the receptions name."""


@dataclass(frozen=True, slots=True)
class ArchivePasses:
    """Every computed pass, and what could not be computed."""

    rows: tuple[Mapping[str, object], ...]
    """In ``(archive_station_id, satellite_id, aos)`` order."""

    counts: Mapping[str, int]
    """``archive_denominator.<reason>``, every reason present, zeros included."""


@dataclass(frozen=True, slots=True)
class _Station:
    archive_station_id: int
    site: GroundSite | None
    first_day: date
    last_day: date
    satellites: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Propagation:
    """What every station's propagation shares: the scope, the service, the tally."""

    since: datetime
    as_of: datetime
    orbit: PassFinder
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class _HeldSet:
    element_set_id: int
    element_set: ElementSet
    retrieved_at: datetime


def compute_archive_passes(
    rows: ArchiveRows, *, since: datetime, as_of: datetime, orbit: PassFinder
) -> ArchivePasses:
    """Propagate every archive station's denominator over the snapshot's scope.

    Args:
        rows: The archive stations, their receptions, and the element sets.
        since: The snapshot's start.
        as_of: The snapshot's instant.
        orbit: The orbit service to propagate with.

    Returns:
        The computed passes and the counts of what could not be computed.
    """
    propagation = _Propagation(
        since=since, as_of=as_of, orbit=orbit, counts=dict.fromkeys(_COUNTS, 0)
    )
    held = _by_satellite(rows.element_sets)
    found: list[tuple[tuple[int, str, datetime], Mapping[str, object]]] = []
    for station in _stations(rows.stations, rows.receptions, propagation.counts):
        if station.site is None:
            continue
        for satellite_id in sorted(station.satellites):
            if satellite_id not in held:
                propagation.counts["satellites_without_element_sets"] += 1
                continue
            found.extend(
                ((station.archive_station_id, satellite_id, window.aos), row)
                for window, row in _satellite_passes(
                    propagation, station, station.site, held[satellite_id]
                )
            )
    return ArchivePasses(
        rows=tuple(row for _, row in sorted(found, key=lambda one: one[0])),
        counts={
            f"archive_denominator.{name}": n for name, n in propagation.counts.items()
        },
    )


def _stations(
    stations: Iterable[Mapping[str, object]],
    receptions: Iterable[Mapping[str, object]],
    counts: dict[str, int],
) -> list[_Station]:
    """Each station with receptions in scope: where it is, when, and what it heard."""
    heard: dict[int, list[Mapping[str, object]]] = {}
    for one in receptions:
        heard.setdefault(_int(one["archive_station_id"]), []).append(one)
    described = {_int(one["archive_station_id"]): one for one in stations}
    result = []
    for station_id in sorted(heard):
        own = heard[station_id]
        days = [_instant(one["started_at"]).astimezone(UTC).date() for one in own]
        norad = {
            str(one["satellite_key"])
            for one in own
            if one["satellite_key_kind"] == "norad"
        }
        others = {
            str(one["satellite_key"])
            for one in own
            if one["satellite_key_kind"] != "norad"
        }
        counts["satellites_not_norad"] += len(others)
        site, altitude_known = _site(described.get(station_id))
        if site is None:
            counts["stations_without_location"] += 1
        elif not altitude_known:
            counts["stations_without_altitude"] += 1
        result.append(
            _Station(
                archive_station_id=station_id,
                site=site,
                first_day=min(days),
                last_day=max(days),
                satellites=frozenset(norad),
            )
        )
    return result


def _site(row: Mapping[str, object] | None) -> tuple[GroundSite | None, bool]:
    """The station's location, and whether its altitude was published."""
    if row is None or row.get("lat_deg") is None or row.get("lon_deg") is None:
        return None, False
    altitude = row.get("alt_m")
    return (
        GroundSite(
            lat_deg=_float(row["lat_deg"]),
            lon_deg=_float(row["lon_deg"]),
            alt_m=0.0 if altitude is None else _float(altitude),
        ),
        altitude is not None,
    )


def _satellite_passes(
    propagation: _Propagation,
    station: _Station,
    site: GroundSite,
    held: Sequence[_HeldSet],
) -> list[tuple[PassWindow, Mapping[str, object]]]:
    """One satellite over one station, day by day across the station's span."""
    rows: list[tuple[PassWindow, Mapping[str, object]]] = []
    day = station.first_day
    while day <= station.last_day:
        starts = datetime.combine(day, time(), tzinfo=UTC)
        current = _current_at(held, starts)
        if current is None:
            propagation.counts["satellite_days_without_element_set"] += 1
        else:
            search = PassSearch(
                element_set=current.element_set,
                site=site,
                start=max(starts, propagation.since),
                end=min(starts + _DAY, propagation.as_of),
            )
            if search.start < search.end:
                rows.extend(
                    (
                        window,
                        _row(
                            station.archive_station_id, current.element_set_id, window
                        ),
                    )
                    for window in propagation.orbit.pass_windows(search)
                )
        day += _DAY
    return rows


def _current_at(held: Sequence[_HeldSet], at: datetime) -> _HeldSet | None:
    """The set current at ``at``: newest epoch not after it, as the store orders."""
    candidates = [one for one in held if one.element_set.epoch <= at]
    return max(
        candidates,
        key=lambda one: (one.element_set.epoch, one.retrieved_at, one.element_set_id),
        default=None,
    )


def _by_satellite(
    element_sets: Iterable[Mapping[str, object]],
) -> dict[str, list[_HeldSet]]:
    held: dict[str, list[_HeldSet]] = {}
    for one in element_sets:
        satellite_id = str(one["satellite_id"])
        held.setdefault(satellite_id, []).append(
            _HeldSet(
                element_set_id=_int(one["id"]),
                element_set=ElementSet(
                    satellite_id=satellite_id,
                    epoch=_instant(one["epoch"]),
                    line1=str(one["line1"]),
                    line2=str(one["line2"]),
                    source=str(one["source"]),
                ),
                retrieved_at=_instant(one["retrieved_at"]),
            )
        )
    return held


def _row(
    archive_station_id: int, element_set_id: int, window: PassWindow
) -> Mapping[str, object]:
    return {
        "archive_station_id": archive_station_id,
        "satellite_id": window.satellite_id,
        "aos": window.aos,
        "los": window.los,
        "max_elevation_deg": window.max_elevation_deg,
        "max_elevation_at": window.max_elevation_at,
        "aos_azimuth_deg": window.aos_azimuth_deg,
        "los_azimuth_deg": window.los_azimuth_deg,
        "element_set_id": element_set_id,
        "min_elevation_deg": window.min_elevation_deg,
    }


def _instant(value: object) -> datetime:
    if not isinstance(value, datetime):
        message = f"expected a timestamp, found {type(value).__name__}"
        raise TypeError(message)
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"expected an integer, found {type(value).__name__}"
        raise TypeError(message)
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"expected a number, found {type(value).__name__}"
        raise TypeError(message)
    return float(value)
